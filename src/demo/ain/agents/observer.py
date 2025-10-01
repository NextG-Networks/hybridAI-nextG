import asyncio
from loguru import logger
from ain.intent.schema import Deviation
from ain.features.minirocket_rt import MiniRocketRT


async def run(
    bus,
    kpi_stream,
    kpi="latency_ms",
    target=10.0,
    model_path="models/minirocket.joblib",
    win=128,
    persist_m=3,
    persist_n=5,
    refractory=2.0,
):
    rt = MiniRocketRT(model_path=model_path, win=win)
    t = 0
    hits = 0
    last_emit = -1e9
    import time

    async for v in kpi_stream:
        out = rt.push(v)
        now = time.time()
        if out and out["pred"] == 1:
            hits += 1
        else:
            hits = max(0, hits - 1)

        if hits >= persist_m and (now - last_emit) >= refractory:
            dev = Deviation(
                kpi=kpi,
                current=float(v),
                target=target,
                scope={"service": "demo", "region": "A"},
                evidence_window=out["window"] if out else [],
                confidence=0.9,
            ).model_dump()
            await bus.pub("deviation.detected", dev)
            last_emit = now
            hits = 0
        t += 1
        await asyncio.sleep(0)
