from __future__ import annotations
from typing import Dict, Optional, List
from ain.intent.schema import Intent


def intent_key(scope: dict, kpi: str) -> str:
    """Single active-intent key: (service, region, kpi)."""
    return f"{scope.get('service','?')}:{scope.get('region','?')}:{kpi}"


class IntentStore:
    """
    In-memory store with a fast index for one-active-intent-per-key.
    """

    def __init__(self) -> None:
        self._by_id: Dict[str, Intent] = {}
        self._by_key: Dict[str, str] = {}  # key -> ACTIVE intent_id

    def put(self, intent: Intent) -> None:
        """Insert/update and maintain the active-by-key index."""
        self._by_id[intent.intent_id] = intent

        # maintain index (use first expectation's KPI as canonical)
        kpi = intent.expectations[0].kpi if intent.expectations else None
        if not kpi:
            return
        k = intent_key(intent.scope, kpi)
        if intent.status == "active":
            self._by_key[k] = intent.intent_id
        else:
            # if this id was indexed for the key but no longer active -> drop
            if self._by_key.get(k) == intent.intent_id:
                self._by_key.pop(k, None)

    def get(self, intent_id: str) -> Optional[Intent]:
        return self._by_id.get(intent_id)

    def get_active_by_key(self, key: str) -> Optional[Intent]:
        """Return the active intent for a given key, if any."""
        iid = self._by_key.get(key)
        return self._by_id.get(iid) if iid else None

    def remove(self, intent_id: str) -> None:
        it = self._by_id.pop(intent_id, None)
        if not it:
            return
        kpi = it.expectations[0].kpi if it.expectations else None
        if not kpi:
            return
        k = intent_key(it.scope, kpi)
        if self._by_key.get(k) == intent_id:
            self._by_key.pop(k, None)

    def all(self) -> List[Intent]:
        return list(self._by_id.values())
