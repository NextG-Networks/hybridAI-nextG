from __future__ import annotations
from typing import Dict, Any
import uuid


class OpenAIError(Exception):
    pass


def reason_from_deviation(deviation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call ChatGPT with a constrained schema and return a 'NetworkIntent'-like dict.
    Replace this stub with your real implementation.

    Expected output keys:
      - intent_id (str)
      - category (e.g., "performance")
      - goal (e.g., "restore_slo")
      - scope {service, region, [cell_id], [slice_id]}
      - constraints {tenancy, change_window, max_risk}
      - slo { latency_ms | thr_dl_bps, loss_pct, availability }
      - evidence_ref (string)
    """
    # TODO: replace with your working LLM call + response parsing.
    # If API key missing or invalid parsing, raise:
    raise OpenAIError("LLM call not configured in this environment")


def _default_scope_for_dev(dev: dict) -> dict:
    scope = dev.get("scope") or {}
    out = {"service": scope.get("service", "demo"), "region": scope.get("region", "A")}
    if scope.get("cell_id"):
        out["cell_id"] = scope["cell_id"]
    if scope.get("slice_id"):
        out["slice_id"] = scope["slice_id"]
    return out


def fallback_intent_for_deviation(dev: dict) -> dict:
    lower = dev.get("direction", "lower_better") == "lower_better"
    latency_mode = lower and ("latency" in dev.get("metric","") or "delay" in dev.get("metric",""))

    slo = {}
    if latency_mode:
        slo["latency_ms"] = float(dev.get("target", dev.get("value", 40.0)))
    else:
        slo["thr_dl_bps"] = float(dev.get("target", 50e6))
    slo.setdefault("loss_pct", 0.1)
    slo.setdefault("availability", 99.9)

    return {
        "intent_id": str(uuid.uuid4()),
        "category": "performance",
        "goal": "restore_slo",
        "scope": _default_scope_for_dev(dev),
        "constraints": {
            "tenancy": (dev.get("scope") or {}).get("tenancy", "prod"),
            "change_window": "22:00Z/2h",
            "max_risk": "low",
        },
        "slo": slo,
        "evidence_ref": dev.get("evidence_ref", "telemetry://window/A"),
    }
