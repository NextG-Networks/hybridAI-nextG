import asyncio, uuid
from loguru import logger
from ain.intent.schema import Intent, Expectation, Report
import time


def intent_key(scope: dict, kpi: str) -> str:
    return f"{scope.get('service','?')}:{scope.get('region','?')}:{kpi}"


class Reasoning:
    def __init__(
        self, bus, target_kpi="latency_ms", target_value=10.0, stable_windows=5
    ):
        self.bus = bus
        self.target_kpi = target_kpi
        self.target_value = target_value
        self.required_stable = stable_windows
        self.intents = {}  # key -> Intent
        self.stable = {}  # key -> consecutive-ok counter
        self.intent_state = {}

    async def handle_deviation(self):
        sub = await self.bus.sub("deviation.detected")
        cooldown = 2.0  # seconds after fulfillment before new intent
        while True:
            dev = await sub.get()
            key = intent_key(dev["scope"], dev["kpi"])
            st = self.intent_state.get(key, {"status": "none", "last_change": 0.0})
            now = time.time()

            # if active, just update version (don’t create new)
            it = self.intents.get(key)
            if it and it.status == "active":
                it.version += 1
                self.intent_state[key] = {"status": "active", "last_change": now}
                await self.bus.pub("intent.set", it.model_dump())
                logger.info(f"Reasoning: updated intent v{it.version} for {key}")
                continue

            # if recently fulfilled/removed, respect cooldown
            if (
                st["status"] in ("fulfilled", "removed")
                and (now - st["last_change"]) < cooldown
            ):
                logger.info(f"Reasoning: cooldown active for {key}, skipping deviation")
                continue

            # create a new active intent
            it = Intent(
                intent_id=str(uuid.uuid4()),
                scope=dev["scope"],
                expectations=[
                    Expectation(
                        kpi=dev["kpi"], op="<=", value=dev["target"], weight=1.0
                    )
                ],
                status="active",
            )
            self.intents[key] = it
            self.stable[key] = 0
            self.intent_state[key] = {"status": "active", "last_change": now}
            logger.info(f"Reasoning: created intent {it.intent_id} for {key}")
            await self.bus.pub("intent.set", it.model_dump())

    async def select_and_dispatch(self):
        plans_sub = await self.bus.sub("plans.candidates")
        est_sub = await self.bus.sub("estimate.report")
        while True:
            batch = await plans_sub.get()
            plans = batch["plans"]
            intent_id = batch["intent_id"]
            # request predictions for each plan
            for p in plans:
                await self.bus.pub(
                    "estimate.request", {"intent_id": intent_id, "plan": p}
                )
            estimates = [await est_sub.get() for _ in plans]

            def utility(e):
                lat = e["predicted"].get(self.target_kpi, 1e9)
                cost = e.get("cost", e.get("plan", {}).get("cost", 0.0))
                risk = e.get("risk", e.get("plan", {}).get("risk", 0.0))
                return (self.target_value - lat) - 0.2 * cost - 0.1 * risk

            best = max(estimates, key=utility)
            await self.bus.pub(
                "plans.selected", {"intent_id": intent_id, "plan": best["plan"]}
            )

    async def consume_act_reports(self):
        sub = await self.bus.sub("act.report")
        while True:
            rep = await sub.get()
            key = intent_key(rep["scope"], self.target_kpi)
            lat = rep["evidence"].get(self.target_kpi, 1e9)
            ok = lat <= self.target_value
            self.stable[key] = self.stable.get(key, 0) + 1 if ok else 0
            logger.info(
                f"Assurance: {self.target_kpi}={lat:.2f} "
                f"(ok={ok}) stable={self.stable[key]}/{self.required_stable}"
            )
            if self.stable[key] >= self.required_stable:
                it = self.intents.get(key)
                if it and it.status == "active":
                    await self.bus.pub(
                        "intent.report",
                        Report(
                            intent_id=it.intent_id,
                            state="fulfilled",
                            evidence=rep["evidence"],
                        ).model_dump(),
                    )
                    it.status = "fulfilled"
                    await self.bus.pub("intent.remove", {"intent_id": it.intent_id})
                    # in consume_act_reports(), after marking fulfilled:
                    self.intent_state[key] = {
                        "status": "fulfilled",
                        "last_change": time.time(),
                    }
                    logger.info(f"Reasoning: removed intent {it.intent_id} (fulfilled)")


async def run(bus, target_kpi="latency_ms", target_value=10.0, stable_windows=5):
    brain = Reasoning(
        bus,
        target_kpi=target_kpi,
        target_value=target_value,
        stable_windows=stable_windows,
    )
    await asyncio.gather(
        brain.handle_deviation(),
        brain.select_and_dispatch(),
        brain.consume_act_reports(),
    )
