import asyncio
from .utils import make_msg

class PredictorAgent:
    def __init__(self, bus, predictor):
        self.bus = bus
        self.model = predictor
        self.state = None

    async def run(self):
        q_state = await self.bus.sub("kpi.window")
        q_cands = await self.bus.sub("proposer.candidates")

        asyncio.create_task(self._online_trainer())

        while True:
            done, pending = await asyncio.wait(
                [asyncio.create_task(q_state.get()), asyncio.create_task(q_cands.get())],
                return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                msg = t.result()
                if msg.topic == "kpi.window":
                    self.state = msg.payload["state"]
                elif msg.topic == "proposer.candidates" and self.state is not None:
                    playbooks = msg.payload["candidates"]
                    scored = self.model.score_playbooks(self.state, playbooks)
                    await self.bus.pub("predictor.scored", make_msg(
                        "predictor.scored", "SCORED", "scored.v1",
                        {"scored": [(pb, float(q)) for pb, q in scored]}
                    ))

    async def _online_trainer(self):
        q = await self.bus.sub("predictor.train.sample")
        while True:
            msg = await q.get()
            loss = self.model.learn_from_sample(msg.payload)
            if loss is not None:
                await self.bus.pub("events.log", make_msg(
                    "events.log", "PREDICTOR_LOSS", "log.v1", {"loss": loss}
                ))
