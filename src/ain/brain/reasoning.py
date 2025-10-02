import asyncio, uuid
from loguru import logger
from ain.intent.schema import Intent, Expectation, Report
import time
import contextlib


def intent_key(scope: dict, kpi: str) -> str:
    return f"{scope.get('service','?')}:{scope.get('region','?')}:{kpi}"


def _field(obj, name, default=None):
    """Get a field from a Pydantic model or a dict safely."""
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


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
        self.pending_create = {}
        self.id_by_key = {}  # key -> intent_id
        self.key_by_id = {}  # intent_id -> key

    async def track_intents(self):
        set_sub = await self.bus.sub("intent.set")
        state_sub = await self.bus.sub("intent.state")

        # create one pending recv task per topic
        set_task = asyncio.create_task(set_sub.get())
        state_task = asyncio.create_task(state_sub.get())

        while True:
            # wait for whichever arrives first
            done, pending = await asyncio.wait(
                {set_task, state_task}, return_when=asyncio.FIRST_COMPLETED
            )

            # exactly one should be done
            done_task = next(iter(done))
            try:
                msg = done_task.result()
            except asyncio.CancelledError:
                # should be rare; restart listeners
                for p in pending:
                    p.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await p
                set_task = asyncio.create_task(set_sub.get())
                state_task = asyncio.create_task(state_sub.get())
                continue

            # ---------- process the message ----------
            if isinstance(msg, dict) and "expectations" in msg and "scope" in msg:
                # Full snapshot
                try:
                    it = Intent(**msg)
                except Exception:
                    it = msg  # be tolerant of schema changes

                kpi = (
                    it.expectations[0].kpi
                    if getattr(it, "expectations", None)
                    else msg.get("expectations", [{}])[0].get("kpi", self.target_kpi)
                )
                scope = it.scope if hasattr(it, "scope") else msg["scope"]
                key = intent_key(scope, kpi)

                prev = self.intents.get(key)
                prev_id = _field(prev, "intent_id") if prev else None
                new_id = _field(it, "intent_id") or (
                    msg.get("intent_id") if isinstance(msg, dict) else None
                )

                self.intents[key] = it
                status = getattr(it, "status", msg.get("status", "active"))
                self.intent_state[key] = {"status": status, "last_change": time.time()}
                iid = _field(it, "intent_id") or (
                    msg.get("intent_id") if isinstance(msg, dict) else None
                )
                logger.info(f"Reasoning: cached intent {iid} for {key}")
                # a create for this key is now satisfied
                self.pending_create.pop(key, None)

                if iid:
                    self.id_by_key[key] = iid
                    self.key_by_id[iid] = key

                if new_id and new_id != prev_id:
                    self.stable[key] = 0  # reset stability counter on new intent

            elif isinstance(msg, dict) and "intent_id" in msg and "status" in msg:
                # Lightweight state update
                iid = msg["intent_id"]
                status = msg["status"]
                key_for_id = None
                scope = msg.get("scope")
                kpi = msg.get("kpi", self.target_kpi)
                if scope and kpi:
                    key_for_id = intent_key(scope, kpi)
                    self.id_by_key[key_for_id] = iid
                    self.key_by_id[iid] = key_for_id

                for key, it in list(self.intents.items()):
                    it_id = _field(it, "intent_id")
                    if it_id == iid:
                        if isinstance(it, Intent):
                            it.status = status
                        else:
                            it["status"] = status
                        self.intent_state[key] = {
                            "status": status,
                            "last_change": time.time(),
                        }
                        if status == "active":
                            self.pending_create.pop(key, None)
                        break
                # -----------------------------------------
                if (
                    key_for_id
                    and status == "active"
                    and key_for_id not in self.intent_state
                ):
                    self.intent_state[key_for_id] = {
                        "status": "active",
                        "last_change": time.time(),
                    }

                if status == "removed":
                    key_rm = self.key_by_id.get(iid)
                    if key_rm:
                        self.id_by_key.pop(key_rm, None)
                        self.intents.pop(key_rm, None)
                        self.stable.pop(key_rm, None)
            # cancel the other pending task and recreate both for next iteration
            for p in pending:
                p.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await p

            set_task = asyncio.create_task(set_sub.get())
            state_task = asyncio.create_task(state_sub.get())

    async def handle_deviation(self):
        sub = await self.bus.sub("deviation.detected")
        cooldown = 2.0  # seconds after fulfillment before new intent
        pending_window = 1.0  # seconds to wait for intent.set after create
        while True:
            dev = await sub.get()
            key = intent_key(dev["scope"], dev["kpi"])
            st = self.intent_state.get(key, {"status": "none", "last_change": 0.0})
            now = time.time()

            # If we just requested an intent.create, wait a bit for it to show up
            if (
                key in self.pending_create
                and (now - self.pending_create[key]) < pending_window
            ):
                logger.info(
                    f"Reasoning: awaiting intent.set for {key}, skipping deviation"
                )
                continue

            # Respect cooldown after fulfillment/removal
            if (
                st["status"] in ("fulfilled", "removed")
                and (now - st["last_change"]) < cooldown
            ):
                logger.info(f"Reasoning: cooldown active for {key}, skipping deviation")
                continue

            # If an active intent exists, nudge it via modify (e.g., refresh expectations/ttl)
            it = self.intents.get(key)
            status = _field(it, "status")
            if it and status == "active":
                iid = _field(it, "intent_id")
                await self.bus.pub(
                    "intent.modify",
                    {
                        "intent_id": iid,
                        "patch": {
                            "expectations": [
                                {
                                    "kpi": dev["kpi"],
                                    "op": "<=",
                                    "value": dev["target"],
                                    "weight": 1.0,
                                }
                            ],
                        },
                    },
                )
                self.intent_state[key] = {"status": "active", "last_change": now}
                logger.info(f"Reasoning: modified intent {iid} for {key}")
                continue

            # Otherwise, request a new intent via ILM
            await self.bus.pub(
                "intent.create",
                {
                    "owner": "detector/observer",
                    "scope": dev["scope"],
                    "expectations": [
                        {
                            "kpi": dev["kpi"],
                            "op": "<=",
                            "value": dev["target"],
                            "weight": 1.0,
                        }
                    ],
                },
            )
            self.pending_create[key] = now
            # Initialize local counters; ILM will emit intent.set shortly
            self.intent_state[key] = {"status": "active", "last_change": now}
            logger.info(f"Reasoning: requested intent.create for {key}")

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

            # update counter first
            cur = self.stable.get(key, 0)
            cur = cur + 1 if ok else 0
            self.stable[key] = cur

            # resolve current intent_id for clearer logs
            it = self.intents.get(key)
            iid = _field(it, "intent_id")
            if not iid:
                iid = self.id_by_key.get(key)

            logger.info(
                f"Assurance[{iid or '–'}]: {self.target_kpi}={lat:.2f} "
                f"(ok={ok}) stable={cur}/{self.required_stable}"
            )

            # Threshold reached → report + request removal
            if cur >= self.required_stable:
                if not iid:
                    logger.error(
                        f"Assurance: reached threshold for {key} but no intent_id; "
                        f"intents.get(key)={it!r}"
                    )
                    # don’t early-return; continue loop
                    continue

                await self.bus.pub(
                    "intent.report",
                    Report(
                        intent_id=iid, state="fulfilled", evidence=rep["evidence"]
                    ).model_dump(),
                )
                logger.info(f"Reasoning: reported fulfilled for {iid}")

                await self.bus.pub("intent.remove", {"intent_id": iid})
                logger.info(f"Reasoning: requested remove for {iid}")

                # mark local state (doesn't change ILM; just for our cooldown logic)
                self.intent_state[key] = {
                    "status": "fulfilled",
                    "last_change": time.time(),
                }
                # (optional) stop counting further on this key until a new intent appears
                # self.stable[key] = 0


async def run(bus, target_kpi="latency_ms", target_value=10.0, stable_windows=5):
    brain = Reasoning(
        bus,
        target_kpi=target_kpi,
        target_value=target_value,
        stable_windows=stable_windows,
    )
    await asyncio.gather(
        brain.track_intents(),
        brain.handle_deviation(),
        brain.select_and_dispatch(),
        brain.consume_act_reports(),
    )
