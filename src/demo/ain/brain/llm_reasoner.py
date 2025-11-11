from __future__ import annotations
import uuid
from typing import Any, Dict

from ain.brain.openai_client import reason_from_deviation, OpenAIError, fallback_intent_for_deviation


def normalize_deviation(event: Dict[str, Any]) -> Dict[str, Any]:
    dev = {
        "source": event.get("source") or "other",
        "metric": event.get("metric") or event.get("metric_name") or "delay_p95_ms",
        "value": float(event.get("value", 0.0)),
        "baseline": event.get("baseline"),
        "target": event.get("target"),
        "direction": event.get("direction") or ("lower_better" if "delay" in (event.get("metric","")) else "higher_better"),
        "severity": event.get("severity") or "medium",
        "scope": {
            "cell_id": (event.get("scope") or {}).get("cell_id"),
            "slice_id": (event.get("scope") or {}).get("slice_id"),
            "region": (event.get("scope") or {}).get("region") or event.get("region"),
            "service": (event.get("scope") or {}).get("service") or event.get("service") or "demo",
            "tenancy": (event.get("scope") or {}).get("tenancy") or "prod",
        },
        "evidence_ref": event.get("evidence_ref"),
    }
    slo = event.get("slo")
    if slo and dev["target"] is None and "target" in slo:
        dev["target"] = float(slo["target"])
    if dev["target"] is None:
        dev["target"] = dev["value"] * (0.8 if dev["direction"] == "lower_better" else 1.2)
    if not dev.get("evidence_ref"):
        dev["evidence_ref"] = "telemetry://window/A"
    return dev


def to_proposer_meta(net_intent: Dict[str, Any]) -> Dict[str, Any]:
    slo = net_intent.get("slo", {})
    metric = "delay_p95_ms" if "latency_ms" in slo else "thr_dl_bps"
    intent_tag = "LATENCY_P95" if metric == "delay_p95_ms" else "THR_DL"
    scope = net_intent.get("scope", {})
    if scope.get("cell_id"):
        scope_str = f"CELL:{scope['cell_id']}"
    elif scope.get("region"):
        scope_str = f"REGION:{scope['region']}"
    else:
        scope_str = "GLOBAL"
    return {"intent": intent_tag, "scope": scope_str}


def to_rl_intent(net_intent: Dict[str, Any]) -> Dict[str, Any]:
    slo = net_intent.get("slo", {})
    if "latency_ms" in slo:
        return {"type": "REDUCE_LATENCY", "metric": "delay_p95_ms", "target": float(slo["latency_ms"]), "direction": "lower_better", "action_cost": 0.01, "reward_clip": 2.0}
    else:
        tgt = float(slo.get("thr_dl_bps", 50e6))
        return {"type": "INCREASE_THROUGHPUT", "metric": "thr_dl_bps", "target": tgt, "direction": "higher_better", "action_cost": 0.01, "reward_clip": 2.0}


def create_network_intent_from_deviation(dev: Dict[str, Any], use_llm: bool = True) -> Dict[str, Any]:
    dev_norm = normalize_deviation(dev)
    if use_llm:
        try:
            ni = reason_from_deviation(dev_norm)
        except OpenAIError:
            ni = fallback_intent_for_deviation(dev_norm)
    else:
        ni = fallback_intent_for_deviation(dev_norm)

    ni.setdefault("intent_id", str(uuid.uuid4()))
    ni.setdefault("category", "performance")
    ni.setdefault("goal", "restore_slo")
    ni.setdefault("scope", {})
    ni["scope"].setdefault("service", dev_norm["scope"]["service"])
    ni["scope"].setdefault("region", dev_norm["scope"]["region"] or "A")
    if dev_norm["scope"].get("cell_id"):
        ni["scope"]["cell_id"] = dev_norm["scope"]["cell_id"]
    if dev_norm["scope"].get("slice_id"):
        ni["scope"]["slice_id"] = dev_norm["scope"]["slice_id"]
    ni.setdefault("constraints", {
        "tenancy": dev_norm["scope"].get("tenancy", "prod"),
        "change_window": "22:00Z/2h",
        "max_risk": "low",
    })
    ni.setdefault("slo", ni.get("slo", {}))
    ni.setdefault("evidence_ref", dev_norm.get("evidence_ref", "telemetry://window/A"))
    return ni
