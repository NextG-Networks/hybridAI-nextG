import asyncio
from .utils import make_msg

class ReasonerAgent:
    def __init__(self, bus, intent):
        self.bus = bus
        self.intent = intent

    async def run(self):
        await self.bus.pub("intent.current", make_msg(
            "intent.current", "INTENT", "intent.v1", self.intent
        ))
        # Keep heartbeat alive
        while True:
            await asyncio.sleep(10)
