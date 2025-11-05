"""
learner/predictor.py
A small GRU-based predictor that scores candidate playbooks given the current state.
- If PyTorch is not installed, it falls back to a simple heuristic scorer.
- Intended for *inference* in the online loop; training hooks are included as stubs.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    TORCH_OK = True
except Exception:
    TORCH_OK = False
    torch = None
    nn = object
    F = None


@dataclass
class Score:
    expected_delta_latency: float  # negative is improvement
    violation_prob: float  # 0..1
    risk: float  # 0..1
    confidence: float  # 0..1


def _featurize_state(state: Dict[str, Any]) -> List[float]:
    # Minimal featurization: pick a few safe keys; fill defaults.
    # You can expand this with MiniRocket features etc.
    feats = []
    feats.append(float(state.get("latency_ms_p95", 50.0)))
    feats.append(float(state.get("loss_pct_p95", 0.1)))
    feats.append(float(state.get("availability", 99.9)))
    feats.append(float(state.get("slo", {}).get("latency_ms", 10.0)))
    # One-hots for vendor/region (toy):
    vendor = state.get("vendor", "nxos")
    region = state.get("region", "A")
    feats += [1.0 if vendor == k else 0.0 for k in ["nxos", "junos", "iosxe"]]
    feats += [1.0 if region == k else 0.0 for k in ["A", "B", "C"]]
    return feats


def _featurize_action(action: Dict[str, Any]) -> List[float]:
    # Convert knobs/toggles to numeric vector.
    # Expected shape like: {"template_id":"pbA","knobs":{"hash_profile":"B","bfd":True,"hold_minutes":10}, "toggles":{"postcheck_strict":True}}
    v = []
    knobs = action.get("knobs", {})
    toggles = action.get("toggles", {})
    # hash_profile one-hot
    hp = knobs.get("hash_profile", "A")
    v += [1.0 if hp == k else 0.0 for k in ["A", "B", "C"]]
    v.append(1.0 if knobs.get("bfd", False) else 0.0)
    v.append(float(knobs.get("hold_minutes", 10)) / 20.0)  # scale
    v.append(1.0 if toggles.get("postcheck_strict", False) else 0.0)
    return v


class _GRUModel(nn.Module):
    def __init__(
        self, in_dim: int, act_dim: int, hidden: int = 64, num_layers: int = 1
    ):
        super().__init__()
        self.gru = nn.GRU(
            input_size=in_dim,
            hidden_size=hidden,
            num_layers=num_layers,
            batch_first=True,
        )
        self.fc_act = nn.Linear(act_dim, hidden)
        self.fc_out = nn.Linear(
            hidden, 4
        )  # [delta_latency, violation_logit, risk, confidence]

    def forward(self, seq, act):
        # seq: (B, T, in_dim), act: (B, act_dim)
        h_seq, _ = self.gru(seq)
        h_last = h_seq[:, -1, :]  # (B, hidden)
        h = h_last + self.fc_act(act)
        y = self.fc_out(h)
        delta_lat = y[:, 0]  # unconstrained
        violation = torch.sigmoid(y[:, 1])  # 0..1
        risk = torch.sigmoid(y[:, 2])  # 0..1
        confidence = torch.sigmoid(y[:, 3])  # 0..1
        return delta_lat, violation, risk, confidence


class GRUPredictor:
    """
    If TORCH_OK False, uses heuristic scoring.
    """

    def __init__(self, input_len: int = 16):
        self.input_len = input_len
        self.model = None
        if TORCH_OK:
            in_dim = (
                3 + 1 + 3 + 3
            )  # latency, loss, availability + slo.lat + vendor one-hot(3) + region one-hot(3)
            act_dim = 3 + 1 + 1 + 1  # hp one-hot(3) + bfd + hold + postcheck_strict
            self.model = _GRUModel(in_dim=in_dim, act_dim=act_dim)
            self.model.eval()

    def score(self, state_seq: List[Dict[str, Any]], action: Dict[str, Any]) -> Score:
        if TORCH_OK and self.model is not None:
            # Build tensors
            import torch

            T = max(1, min(len(state_seq), self.input_len))
            seq = state_seq[-T:]
            X = [_featurize_state(s) for s in seq]
            A = _featurize_action(action)
            xt = torch.tensor([X], dtype=torch.float32)  # (1,T,D)
            at = torch.tensor([A], dtype=torch.float32)  # (1,A)
            with torch.no_grad():
                dlat, viol, risk, conf = self.model(xt, at)
            return Score(
                expected_delta_latency=float(dlat.item()),
                violation_prob=float(viol.item()),
                risk=float(risk.item()),
                confidence=float(conf.item()),
            )
        else:
            # Heuristic: assume BFD on + stricter postcheck improves latency a bit but increases risk tiny.
            base_lat = float(state_seq[-1].get("latency_ms_p95", 50.0))
            slo_lat = float(state_seq[-1].get("slo", {}).get("latency_ms", 10.0))
            gap = base_lat - slo_lat
            delta = -0.15 * gap  # aim to reduce 15% of gap
            if action.get("knobs", {}).get("bfd", False):
                delta *= 1.1
            if action.get("toggles", {}).get("postcheck_strict", False):
                delta *= 1.05
            risk = 0.05 + 0.02 * (
                1.0 if action.get("knobs", {}).get("bfd", False) else 0.0
            )
            conf = 0.7
            viol_prob = 1.0 if (base_lat + delta) > slo_lat else 0.2
            return Score(
                expected_delta_latency=delta,
                violation_prob=viol_prob,
                risk=risk,
                confidence=conf,
            )

    # Stubs for training hooks (fill later)
    def update_from_episode(self, experience: Dict[str, Any]) -> None:
        pass

    def load(self, path: str) -> None:
        if TORCH_OK and self.model is not None:
            self.model.load_state_dict(torch.load(path, map_location="cpu"))

    def save(self, path: str) -> None:
        if TORCH_OK and self.model is not None:
            torch.save(self.model.state_dict(), path)
