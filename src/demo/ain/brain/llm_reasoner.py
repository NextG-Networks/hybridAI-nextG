"""
LLM Reasoner loop:
- Subscribes to 'deviation.detected' from the Observer
- Calls OpenAI to produce an Intent JSON (schema-constrained)
- Publishes 'intent.set' for the Proposer
"""

import asyncio
import uuid
from loguru import logger
from typing import Dict, Any
from .openai_client import reason_from_deviation, OpenAIError


async def run(bus, target_kpi: str = "latency_ms"):
    sub = await bus.sub("deviation.detected")
    backoff = 1.0  # seconds, grows on repeated failures up to max_backoff
    max_backoff = 15.0

    while True:
        dev = await sub.get()
        try:
            intent = reason_from_deviation(dev)
            intent.setdefault("intent_id", str(uuid.uuid4()))
            # be nice: if missing, inject a default slo target from the deviation
            # scope
            intent.setdefault("scope", {})
            intent["scope"].setdefault(
                "service", dev.get("scope", {}).get("service", "demo")
            )
            intent["scope"].setdefault(
                "region", dev.get("scope", {}).get("region", "A")
            )

            # constraints
            intent.setdefault("constraints", {})
            intent["constraints"].setdefault(
                "tenancy", dev.get("scope", {}).get("tenancy", "prod")
            )
            intent["constraints"].setdefault("change_window", "22:00Z/2h")
            intent["constraints"].setdefault("max_risk", "low")

            # slo
            intent.setdefault("slo", {})
            intent["slo"].setdefault("latency_ms", dev.get("target", 10.0))
            intent["slo"].setdefault("loss_pct", 0.1)
            intent["slo"].setdefault("availability", 99.9)

            # evidence
            intent.setdefault(
                "evidence_ref",
                f"telemetry://window/{dev.get('scope', {}).get('region', 'A')}",
            )

            if target_kpi and target_kpi not in intent["slo"] and "target" in dev:
                intent["slo"][target_kpi] = dev["target"]

            await bus.pub("intent.set", intent)
            logger.info(f"LLM Reasoner: emitted intent {intent['intent_id']}")
            backoff = 1.0  # reset on success
        except OpenAIError as e:
            # replace angle brackets to avoid Loguru color-tag parsing
            safe = str(e).replace("<", "(").replace(">", ")")
            logger.error("LLM Reasoner failed: {}", safe)
            await asyncio.sleep(backoff)
            backoff = min(max_backoff, backoff * 1.7)
        except Exception as e:
            logger.exception(f"LLM Reasoner unexpected error: {e}")
            await asyncio.sleep(backoff)
            backoff = min(max_backoff, backoff * 1.7)
