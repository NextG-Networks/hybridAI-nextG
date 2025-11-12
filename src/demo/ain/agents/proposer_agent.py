import asyncio, random
from .utils import make_msg

class ProposerAgent:
    def __init__(self, bus, action_space):
        self.bus = bus
        self.action_space = action_space
        self.intent = None

    async def run(self):
        q_state = await self.bus.sub("kpi.window")
        q_intent = await self.bus.sub("intent.current")
        asyncio.create_task(self._listen_intent(q_intent))

        while True:
            msg = await q_state.get()
            if not self.intent: continue
            epsilon = 0.3  # can later decay dynamically
            playbooks = ProposerSampler.sample_playbooks(
                self.action_space, epsilon=epsilon,
                intent_meta=self.intent, cache=None
            )
            await self.bus.pub("proposer.candidates", make_msg(
                "proposer.candidates", "PLAYBOOKS", "playbooks.v1",
                {"candidates": [pb.to_json() for pb in playbooks]}
            ))

    async def _listen_intent(self, q):
        while True:
            m = await q.get()
            self.intent = m.payload
