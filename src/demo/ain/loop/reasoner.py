
"""
reasoner.py — Minimal "Reasoner" stub.

In production, this would call an LLM (e.g., OpenAI GPT) with the SLO intent text
and recent KPIs to produce a structured Intent. Here we provide a simple
rule-based fallback with the same interface, so the demo can run offline.

You can later swap Reasoner.create_intent() to use an API client.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict

@dataclass
class ReasonerConfig:
    # placeholder fields for future LLM usage
    provider: str = "stub"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2

class Reasoner:
    def __init__(self, cfg: ReasonerConfig):
        self.cfg = cfg

    def create_intent(self, slo: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a structured intent dict compatible with RLObserver.Intent.
        Example output:
          {"type":"REDUCE_LATENCY","metric":"delay_p95_ms","target":40.0,
           "direction":"lower_better","action_cost":0.01,"reward_clip":2.0}
        """
        objective = (slo.get("objective") or "REDUCE_LATENCY").upper()
        metric = slo.get("metric", "delay_p95_ms")
        target = float(slo.get("target", 40.0))

        if "THROUGHPUT" in objective:
            return {
                "type": "INCREASE_THROUGHPUT",
                "metric": "thr_dl_bps",
                "target": 50e6,
                "direction": "higher_better",
                "action_cost": 0.01,
                "reward_clip": 2.0,
            }
        else:
            return {
                "type": "REDUCE_LATENCY",
                "metric": metric,
                "target": target,
                "direction": "lower_better",
                "action_cost": 0.01,
                "reward_clip": 2.0,
            }
