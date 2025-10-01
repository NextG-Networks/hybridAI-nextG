import asyncio
from collections import defaultdict
from typing import Callable, Dict, List, Tuple, Any


class MemBus:
    def __init__(self):
        self._subs: List[Tuple[str, asyncio.Queue]] = []

    def subscribe(self, topic_prefix: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append((topic_prefix, q))
        return q

    async def publish(self, topic: str, payload: Any):
        # fan-out to all matching prefixes
        for prefix, q in self._subs:
            if topic.startswith(prefix):
                await q.put((topic, payload))
