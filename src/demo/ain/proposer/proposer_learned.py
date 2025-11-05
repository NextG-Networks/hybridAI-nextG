"""
proposer/proposer_learned.py
Listens to 'intent.set', creates K mutated playbooks, scores them with Predictor,
then publishes the best one on 'execution.commands'.
"""

from __future__ import annotations
import asyncio, uuid
from typing import Dict, Any, List
from loguru import logger

from ain.learner.predictor import GRUPredictor
from ain.learner.objective import rank
from ain.proposer.mutate import generate


# Convert intent to minimal "state" dict for predictor
def state_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    slo = intent.get("slo", {})
    scope = intent.get("scope", {})
    st = {
        "latency_ms_p95": intent.get("current_latency_ms_p95", 50.0),
        "loss_pct_p95": intent.get("current_loss_pct_p95", 0.1),
        "availability": intent.get("current_availability", 99.9),
        "slo": {"latency_ms": slo.get("latency_ms", 10.0)},
        "vendor": intent.get("vendor", "nxos"),
        "region": scope.get("region", "A"),
    }
    return st


async def run(bus, k: int = 3):
    intents = await bus.sub("intent.set")
    predictor = GRUPredictor()
    history = []  # short state history for GRU

    while True:
        it = await intents.get()
        st = state_from_intent(it)
        history.append(st)
        history = history[-16:]  # keep last 16

        base_pb = {
            "template_id": "pbA",
            "knobs": {"hash_profile": "A", "bfd": True, "hold_minutes": 10},
            "toggles": {"postcheck_strict": True},
        }
        cands = generate(base_pb, k=k)

        # Score candidates
        scores = [predictor.score(history, c) for c in cands]
        order = rank(scores)
        best = cands[order[0]]

        # Attach metadata and publish
        command = {
            "command_id": str(uuid.uuid4()),
            "intent_id": it.get("intent_id", str(uuid.uuid4())),
            "playbook": best,
            "policy": {"reason": "learned_predictor_min_cost"},
        }
        await bus.pub("execution.commands", command)
        logger.info(
            f"Proposer: published best playbook variant for intent {command['intent_id']}"
        )
