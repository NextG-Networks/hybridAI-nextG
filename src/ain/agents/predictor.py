import asyncio, random
from ain.intent.schema import Estimate
from loguru import logger


async def run(bus, horizon_s=60):
    rq = await bus.sub("estimate.request")
    while True:
        req = await rq.get()
        plan = req["plan"]
        # naive refinement: jitter the predicted latency slightly
        lat = max(0.0, plan["predicted"]["latency_ms"] + random.uniform(-0.3, 0.3))
        est = Estimate(
            plan_id=plan["plan_id"],
            horizon_s=horizon_s,
            predicted={"latency_ms": lat},
            confidence=0.8,
        ).model_dump()
        # bubble-through plan metadata for scoring
        est["cost"] = plan["cost"]
        est["risk"] = plan["risk"]
        est["plan"] = plan
        await bus.pub("estimate.report", est)
        logger.info(f"Predictor: {plan['plan_id']} → {lat:.2f} ms")
