# src/demo/ain/bus/mem.py
import asyncio
from collections import defaultdict
from typing import Any, Dict, List


class MemBus:
    def __init__(self) -> None:
        self._topics: Dict[str, List[asyncio.Queue]] = defaultdict(list)

    async def pub(self, topic: str, payload: Any) -> None:
        for q in list(self._topics.get(topic, [])):
            await q.put(payload)

    async def sub(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._topics[topic].append(q)
        return q

    def unsub(self, topic: str, q: asyncio.Queue) -> None:
        if q in self._topics.get(topic, []):
            self._topics[topic].remove(q)
