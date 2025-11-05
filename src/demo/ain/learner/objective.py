"""
learner/objective.py
Risk-aware objective J(a) to pick among candidate playbooks using predictor scores.
"""

from __future__ import annotations
from typing import List, Dict, Any
from .predictor import Score


def cost(score: Score, weights=None) -> float:
    """
    Lower is better.
    By default, we encourage negative delta (improvement) and penalize risk/violations.
    """
    if weights is None:
        weights = {"delta": 1.0, "viol": 2.5, "risk": 1.5, "conf": 0.2}
    # delta is negative when improving; subtract it to prefer improvements
    j = (
        weights["delta"] * (-score.expected_delta_latency)
        + weights["viol"] * (score.violation_prob)
        + weights["risk"] * (score.risk)
        - weights["conf"] * (score.confidence)
    )
    return float(j)


def rank(scores: List[Score]) -> List[int]:
    """
    Returns indices sorted by ascending cost.
    """
    costs = [cost(s) for s in scores]
    return sorted(range(len(scores)), key=lambda i: costs[i])
