import asyncio
from .utils import make_msg

class ActorAgent:
    def __init__(self, bus, actor):
        self.bus = bus
        self.actor = actor

    async def run(self):
        q = await self.bus.sub("predictor.scored")
        while True:
            msg = await q.get()
            best_json, best_q = max(msg.payload["scored"], key=lambda t: t[1])
            await self.bus.pub("actor.apply", make_msg(
                "actor.apply", "APPLY", "actor.apply.v1",
                {"playbook": best_json, "q": best_q}
            ))
            self.actor.apply(best_json)
