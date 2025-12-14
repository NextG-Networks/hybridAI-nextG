import asyncio
from datetime import datetime, timezone
from .utils import make_msg

class RLObserverAgent:
    def __init__(self, bus, intent=None, window=12):
        self.bus = bus
        self.window = window
        self.intent = intent
        self._buf = []

    async def run(self):
        q = await self.bus.sub("kpi.raw")
        while True:
            msg = await q.get()
            snap = msg.payload
            self._buf.append(snap)
            self._buf = self._buf[-self.window:]

            # convert KPI list -> state + reward
            state = self._vectorize(self._buf)
            reward = self._compute_reward(snap)
            await self.bus.pub(
                "kpi.window",
                make_msg("kpi.window", "STATE_WINDOW", "kpi.window.v1",
                         {"state": state, "reward": reward})
            )

    def _vectorize(self, buf):
        # Replace this with your real feature builder
        return [v.get("delay_p95_ms", 0) for v in buf]

    def _compute_reward(self, kpi):
        if not self.intent:
            return 0.0
        target = self.intent["target"]
        metric = kpi[self.intent["metric"]]
        if self.intent["direction"] == "lower_better":
            return max(0, (target - metric) / target)
        else:
            return max(0, (metric - target) / target)
