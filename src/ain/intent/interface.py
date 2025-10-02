from __future__ import annotations
import asyncio, uuid
from datetime import datetime, timezone
from loguru import logger
from ain.intent.store import IntentStore, intent_key
from ain.intent.schema import Intent, Expectation, ModifyPatch


def _field(obj, name, default=None):
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _first_kpi(it):
    exps = _field(it, "expectations") or []
    return exps[0].kpi if exps else None


def _safe_update_pydantic(obj, updates: dict):
    existing_fields = set(
        getattr(obj, "__fields__", getattr(obj, "model_fields", {})) or {}
    )
    filtered = {
        k: v
        for k, v in updates.items()
        if (not existing_fields) or (k in existing_fields)
    }

    if hasattr(obj, "model_copy"):  # pydantic v2
        return obj.model_copy(update=filtered)
    if hasattr(obj, "copy"):  # pydantic v1
        return obj.copy(update=filtered)

    for k, v in filtered.items():
        try:
            setattr(obj, k, v)
        except Exception:
            try:
                obj[k] = v  # type: ignore[index]
            except Exception:
                pass
    return obj


class IntentInterface:
    """
    Message-driven ILM. Subscribes to UI/API/policy triggers and manages lifecycle.
    IN : intent.create, intent.modify, intent.remove, intent.report
    OUT: intent.set (snapshot), intent.state, intent.error
    """

    def __init__(self, bus, store: IntentStore):
        self.bus = bus
        self.store = store

    async def _create_loop(self):
        sub = await self.bus.sub("intent.create")
        logger.info("ILM: listening on intent.create")
        while True:
            payload = await sub.get()
            try:
                logger.debug(f"ILM: received create payload: {payload}")
                scope = payload["scope"]
                exps_in = payload["expectations"]
                if not exps_in:
                    raise ValueError("expectations[] required")
                exps = [Expectation(**e) for e in exps_in]

                k = intent_key(scope, exps[0].kpi)

                # 1) Fast path: active-by-key index
                existing = self.store.get_active_by_key(k)

                # 2) Fallback scan (robust even if index lagged)
                if not existing:
                    for it in self.store.all():
                        if _field(it, "status") != "active":
                            continue
                        kpi0 = _field(it, "expectations", [])
                        kpi0 = kpi0[0].kpi if kpi0 else None
                        if not kpi0:
                            continue
                        if intent_key(_field(it, "scope"), kpi0) == k:
                            existing = it
                            break

                if existing:
                    # Optional upsert
                    updates = {}
                    if exps:
                        updates["expectations"] = exps
                    if hasattr(existing, "version"):
                        updates["version"] = _field(existing, "version", 0) + 1
                    if hasattr(existing, "updated_at"):
                        updates["updated_at"] = datetime.now(timezone.utc)
                    if "resources" in payload:
                        updates["resources"] = payload["resources"]
                    if "owner" in payload:
                        updates["owner"] = payload["owner"]

                    existing = _safe_update_pydantic(existing, updates)
                    self.store.put(existing)

                    out = (
                        existing.model_dump()
                        if hasattr(existing, "model_dump")
                        else existing
                    )
                    await self.bus.pub("intent.set", out)
                    await self.bus.pub(
                        "intent.state",
                        {
                            "intent_id": _field(existing, "intent_id"),
                            "status": _field(existing, "status", "active"),
                            "scope": _field(existing, "scope"),
                            "kpi": _first_kpi(existing),
                        },
                    )
                    logger.info(
                        f"ILM: reused active intent {_field(existing,'intent_id')} for {k}"
                    )
                    continue

                # Create new active intent (no hard dependency on created_at/updated_at)
                kwargs = dict(
                    intent_id=payload.get("intent_id", str(uuid.uuid4())),
                    scope=scope,
                    expectations=exps,
                    status="active",
                    owner=payload.get("owner"),
                    resources=payload.get("resources", []),
                )
                try:
                    # Try with timestamps if the model supports them
                    intent = Intent(
                        **{
                            **kwargs,
                            "created_at": datetime.now(timezone.utc),
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
                except Exception:
                    # Fall back to minimal fields
                    intent = Intent(**kwargs)

                self.store.put(intent)
                out = intent.model_dump() if hasattr(intent, "model_dump") else intent
                await self.bus.pub("intent.set", out)
                await self.bus.pub(
                    "intent.state",
                    {
                        "intent_id": _field(intent, "intent_id"),
                        "status": "active",
                        "scope": _field(existing, "scope"),
                        "kpi": _first_kpi(existing),
                    },
                )
                logger.info(f"ILM: created {_field(intent,'intent_id')} for {k}")
            except Exception as e:
                await self.bus.pub(
                    "intent.error",
                    {"op": "create", "error": str(e), "payload": payload},
                )

    async def _modify_loop(self):
        sub = await self.bus.sub("intent.modify")
        while True:
            payload = await sub.get()
            try:
                it = self.store.get(payload["intent_id"])
                if not it:
                    raise KeyError("not_found")

                patch = ModifyPatch(**payload.get("patch", {}))
                updates = {}
                if patch.expectations is not None:
                    updates["expectations"] = patch.expectations
                if patch.scope is not None:
                    updates["scope"] = patch.scope
                if patch.resources is not None:
                    updates["resources"] = patch.resources
                if hasattr(it, "version"):
                    updates["version"] = _field(it, "version", 0) + 1
                if hasattr(it, "updated_at"):
                    updates["updated_at"] = datetime.now(timezone.utc)
                if _field(it, "status") in ("fulfilled", "violated", "removed"):
                    updates["status"] = "active"

                it = _safe_update_pydantic(it, updates)
                self.store.put(it)

                out = it.model_dump() if hasattr(it, "model_dump") else it
                await self.bus.pub("intent.set", out)
                await self.bus.pub(
                    "intent.state",
                    {
                        "intent_id": _field(it, "intent_id"),
                        "status": _field(it, "status"),
                        "scope": _field(it, "scope"),
                        "kpi": _first_kpi(it),
                    },
                )
                logger.info(
                    f"ILM: modified {_field(it,'intent_id')} → v{_field(it,'version','?')}"
                )
            except Exception as e:
                await self.bus.pub(
                    "intent.error",
                    {"op": "modify", "error": str(e), "payload": payload},
                )

    async def _remove_loop(self):
        sub = await self.bus.sub("intent.remove")
        while True:
            payload = await sub.get()
            try:
                it = self.store.get(payload["intent_id"])
                if not it:
                    raise KeyError("not_found")

                updates = {"status": "removed"}
                if hasattr(it, "updated_at"):
                    updates["updated_at"] = datetime.now(timezone.utc)
                it = _safe_update_pydantic(it, updates)

                iid = _field(it, "intent_id")
                await self.bus.pub(
                    "intent.state",
                    {
                        "intent_id": iid,
                        "status": "removed",
                        "scope": _field(it, "scope"),
                        "kpi": _first_kpi(it),
                    },
                )
                self.store.remove(iid)
                logger.info(f"ILM: removed {iid}")
            except Exception as e:
                await self.bus.pub(
                    "intent.error",
                    {"op": "remove", "error": str(e), "payload": payload},
                )

    async def _report_loop(self):
        sub = await self.bus.sub("intent.report")
        while True:
            rep = await sub.get()
            try:
                it = self.store.get(rep["intent_id"])
                if not it:
                    raise KeyError("unknown_intent")

                state = rep.get("state")
                cur_status = _field(it, "status")
                updates = {}

                if state == "in_progress" and cur_status == "active":
                    updates["status"] = "in_progress"
                elif state == "fulfilled":
                    updates["status"] = "fulfilled"
                elif state == "violated":
                    updates["status"] = "violated"

                if hasattr(it, "updated_at"):
                    updates["updated_at"] = datetime.now(timezone.utc)

                it = _safe_update_pydantic(it, updates)
                self.store.put(it)

                await self.bus.pub(
                    "intent.state",
                    {
                        "intent_id": _field(it, "intent_id"),
                        "status": _field(it, "status"),
                        "evidence": rep.get("evidence", {}),
                        "scope": _field(it, "scope"),
                        "kpi": _first_kpi(it),
                    },
                )
            except Exception as e:
                await self.bus.pub(
                    "intent.error", {"op": "report", "error": str(e), "payload": rep}
                )

    async def run(self):
        await asyncio.gather(
            self._create_loop(),
            self._modify_loop(),
            self._remove_loop(),
            self._report_loop(),
        )
