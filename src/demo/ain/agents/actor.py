import asyncio, random
from loguru import logger


async def run(bus, scope={"service": "demo", "region": "A"}):
    sub = await bus.sub("plans.selected")
    while True:
        sel = await sub.get()
        plan = sel["plan"]
        # pretend execution; measure around predicted with slight noise
        measured = max(0.0, plan["predicted"]["latency_ms"] + random.uniform(-0.2, 0.2))
        rep = {
            "intent_id": sel["intent_id"],
            "scope": scope,
            "evidence": {"latency_ms": measured},
            "state": "in_progress",
        }
        await bus.pub("act.report", rep)
        logger.info(f"Actor: executed {plan['plan_id']} → {measured:.2f} ms")
