import asyncio
from collections import defaultdict


class MemBus:
    def __init__(self):
        self.topics = defaultdict(asyncio.Queue)

    async def pub(self, topic: str, msg):
        await self.topics[topic].put(msg)

    async def sub(self, topic: str) -> asyncio.Queue:
        return self.topics[topic]
