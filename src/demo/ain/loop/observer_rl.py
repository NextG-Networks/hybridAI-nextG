
# observer_rl.py
from __future__ import annotations
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import torch


@dataclass
class Intent:
    type: str                 # e.g., "REDUCE_LATENCY", "INCREASE_THROUGHPUT"
    metric: str               # e.g., "delay_p95_ms"
    target: float             # e.g., 40.0
    direction: str = "lower_better"  # "lower_better" | "higher_better"
    action_cost: float = 0.01        # penalty per action in playbook
    reward_clip: float = 2.0         # clip absolute reward

class RLObserver:
    def __init__(
        self,
        predictor,
        intent: Intent,
        kpi_file: str = "fake_kpi_stream.json",
        window: int = 12,
        features: Optional[List[str]] = None,
        use_internal_encoder: bool = False,
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
            self.predictor.learn_step()


        # Update previous pointers
        self.last_kpi_raw = kpi

        return s2  # current window

if __name__ == "__main__":
    pass
