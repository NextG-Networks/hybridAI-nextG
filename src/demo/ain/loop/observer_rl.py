
# observer_rl.py
from __future__ import annotations
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import torch

# Optional: if your project path exposes these, you can import them.
# Fallback to local definition if not available at import time.
try:
    from ain.loop.predictor import StateEncoder  # type: ignore
except Exception:
    import torch.nn as nn
    class StateEncoder(nn.Module):
        def __init__(self, feat_dim: int, hidden: int = 64):
            super().__init__()
            self.gru = nn.GRU(input_size=feat_dim, hidden_size=hidden, num_layers=1, batch_first=True)
        def forward(self, x):
            _, h = self.gru(x)
            return h.squeeze(0)

@dataclass
class Intent:
    type: str                 # e.g., "REDUCE_LATENCY", "INCREASE_THROUGHPUT"
    metric: str               # e.g., "delay_p95_ms"
    target: float             # e.g., 40.0
    direction: str = "lower_better"  # "lower_better" | "higher_better"
    action_cost: float = 0.01        # penalty per action in playbook
    reward_clip: float = 2.0         # clip absolute reward

class RLObserver:
    """
    RL-focused Observer.

    Responsibilities:
    - Reads KPI JSON snapshots from a rolling file (like fake_kpi_stream.json).
    - Maintains a sliding window of the last W feature vectors.
    - Exposes state windows as np.ndarray [W, F] for the Predictor.
    - Optionally encodes the window with an internal GRU StateEncoder to produce embeddings.
    - Computes rewards based on active Intent and KPI deltas.
    - Pushes (s, p, r, s2, done) to predictor.replay and calls predictor.learn_step().
    """
    def __init__(
        self,
        predictor,
        intent: Intent,
        kpi_file: str = "fake_kpi_stream.json",
        window: int = 12,
        features: Optional[List[str]] = None,
        use_internal_encoder: bool = False,
        state_hidden: int = 64,
        device: Optional[torch.device] = None,
    ):
        self.predictor = predictor
        self.intent = intent
        self.kpi_file = Path(kpi_file)
        self.window = window
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.buf: Deque[np.ndarray] = deque(maxlen=window)
        self.last_kpi_raw: Optional[Dict] = None
        self.last_state_win: Optional[np.ndarray] = None  # [W, F]
        self.use_internal_encoder = use_internal_encoder

        # Select features (defaults chosen from your schema)
        self.features = features or [
            "thr_dl_bps",
            "thr_ul_bps",
            "bler_dl",
            "bler_ul",
            "cqi_avg",
            "mcs_dl_avg",
            "mcs_ul_avg",
            "active_ue_count",
            "prb_used_dl_ratio",
            "delay_p95_ms",
        ]

        # Simple fixed scalers (adjust as you like)
        self.scalers = {
            "thr_dl_bps": 1e6,  # scale to Mbps
            "thr_ul_bps": 1e6,
            "cqi_avg": 15.0,
            "mcs_dl_avg": 28.0,
            "mcs_ul_avg": 28.0,
            "active_ue_count": 100.0,
            "delay_p95_ms": 100.0,
        }

        # Internal encoder (optional). NOTE: Predictor already includes a StateEncoder;
        # use this only if you want an embedding in the Observer for other purposes.
        self.state_enc = None
        if self.use_internal_encoder:
            feat_dim = len(self.features)
            self.state_enc = StateEncoder(feat_dim=feat_dim, hidden=state_hidden).to(self.device)
            self.state_enc.eval()

    # ---------- KPI reading & preprocessing ----------
    def _read_latest_kpi(self) -> Optional[Dict]:
        if not self.kpi_file.exists():
            return None
        with open(self.kpi_file, "r") as f:
            data = json.load(f)
        stream = data.get("kpi_stream", [])
        if not stream:
            return None
        return stream[-1]

    def _extract_features_row(self, kpi: Dict) -> np.ndarray:
        cell = kpi.get("CellMetrics", {})
        # derived ratios
        prb_total = max(float(cell.get("prb_total", 1.0)), 1.0)
        prb_used_dl = float(cell.get("prb_used_dl", 0.0))
        # build feature vector in configured order
        vals: List[float] = []
        for name in self.features:
            if name == "prb_used_dl_ratio":
                v = prb_used_dl / prb_total
            else:
                v = float(cell.get(name, 0.0))
            # scale
            scale = self.scalers.get(name, 1.0)
            v = v / scale
            vals.append(v)
        return np.asarray(vals, dtype=np.float32)

    def _update_window(self, row: np.ndarray) -> np.ndarray:
        from copy import deepcopy
        self.buf.append(row)
        if len(self.buf) < self.window:
            # Left-pad with the first row until window is full (simple pad)
            first = deepcopy(self.buf[0])
            padded = [first] * (self.window - len(self.buf)) + list(self.buf)
            win = np.stack(padded, axis=0)
        else:
            win = np.stack(list(self.buf), axis=0)
        self.last_state_win = win
        return win  # [W, F]

    # ---------- Reward ----------
    def _compute_reward(self, prev_kpi: Dict, curr_kpi: Dict, actions_len: int) -> float:
        metric = self.intent.metric
        target = self.intent.target
        direction = self.intent.direction

        prev = float(prev_kpi.get("CellMetrics", {}).get(metric, 0.0))
        curr = float(curr_kpi.get("CellMetrics", {}).get(metric, 0.0))

        if direction == "lower_better":
            delta_raw = (prev - curr)
            delta_rel = delta_raw / max(abs(prev), 1e-6)
            violation = 1.0 if curr > target else 0.0
        else:  # higher_better
            delta_raw = (curr - prev)
            delta_rel = delta_raw / max(abs(prev), 1e-6)
            violation = 1.0 if curr < target else 0.0

        r = delta_rel
        r -= self.intent.action_cost * float(actions_len)
        r -= violation  # penalty if target not met
        clip = self.intent.reward_clip
        return float(np.clip(r, -clip, clip))

    # ---------- Public API ----------
    def step(self, last_playbook) -> Optional[np.ndarray]:
        """
        Read the latest KPI, update the sliding window, compute reward (if possible),
        and train the predictor using the new transition.

        Returns: current state window [W, F] or None if no KPI yet.
        """
        kpi = self._read_latest_kpi()
        if kpi is None:
            return None

        row = self._extract_features_row(kpi)
        s2 = self._update_window(row)  # [W, F]

        # If we have a previous KPI, compute reward and push to replay
        if self.last_kpi_raw is not None and self.last_state_win is not None:
            s = self.last_state_win  # previous window [W, F]
            r = self._compute_reward(self.last_kpi_raw, kpi, len(getattr(last_playbook, "actions", [])))
            p = self.predictor.encode_playbook_onehot(last_playbook)  # [K, D]

            # Push transition to replay and learn
            self.predictor.replay.push(s, p, r, s2, False)
            self.predictor.learn_step(feat_dim=s.shape[1])

        # Update previous pointers
        self.last_kpi_raw = kpi

        return s2  # current window

    # Optional: produce an embedding with internal StateEncoder (if enabled)
    def encode_state_embedding(self) -> Optional[np.ndarray]:
        if not self.use_internal_encoder or self.state_enc is None or self.last_state_win is None:
            return None
        x = torch.tensor(self.last_state_win[None, ...], dtype=torch.float32, device=self.device)  # [1, W, F]
        with torch.no_grad():
            z = self.state_enc(x)  # [1, H]
        return z.detach().cpu().numpy().squeeze(0)

# Demo (optional) — run this file directly to smoke-test windowing and rewards.
if __name__ == "__main__":
    from dataclasses import dataclass

    class DummyPredictor:
        def __init__(self):
            class DummyReplay:
                def __init__(self): self.data = []
                def push(self, s, p, r, s2, d): self.data.append((s, p, r, s2, d))
            self.replay = DummyReplay()
            self.steps = 0
        def encode_playbook_onehot(self, pb):
            return np.zeros((3, 48), dtype=np.float32)
        def learn_step(self, feat_dim):
            self.steps += 1

    @dataclass
    class Playbook:
        actions: List[dict]

    intent = Intent(type="REDUCE_LATENCY", metric="delay_p95_ms", target=40.0, direction="lower_better")
    obs = RLObserver(DummyPredictor(), intent=intent, kpi_file="fake_kpi_stream.json", window=12)
    # Simulate a few steps with a dummy playbook
    for _ in range(5):
        obs.step(Playbook(actions=[{"type": "MCS_CAP"}]))
    print(f"Collected transitions: {len(obs.predictor.replay.data)}")
