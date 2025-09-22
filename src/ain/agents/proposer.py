import asyncio, uuid
from loguru import logger


async def run(bus):
    isub = await bus.sub("intent.set")
    while True:
        it = await isub.get()
        # two sample plans with different trade-offs
        plans = [
            {
                "plan_id": str(uuid.uuid4()),
                "actions": [{"reroute": 1}],
                "predicted": {"latency_ms": 8.8},
                "cost": 1.0,
                "risk": 0.25,
                "resources": ["path/A"],
            },
            {
                "plan_id": str(uuid.uuid4()),
                "actions": [{"sched_weight_delta": -0.1}],
                "predicted": {"latency_ms": 9.5},
                "cost": 0.4,
                "risk": 0.35,
                "resources": ["node/*"],
            },
        ]
        await bus.pub(
            "plans.candidates", {"intent_id": it["intent_id"], "plans": plans}
        )
        logger.info("Proposer: emitted 2 candidate plans")
