# (DEV)
from pathlib import Path
import pandas as pd, asyncio
from core.membus import AsyncBus
from src.core.schemas import FeatureBS
from src.observer.feature_box import FeatureBox
from src.core.utils import now_s

class SimSource(DataSource):
    def __init__(self, bus: AsyncBus, root: Path, scenario: dict, tick_ms: int):
        self.bus = bus
        self.root = root
        self.tr, self.exp, self.bs = scenario["tr"], scenario["exp"], scenario["bs"]
        self._sched = scenario["sched"]
        self.tick = tick_ms/1000
        # preload all sched streams for instant switching
        self._streams = {s: pd.read_csv(root / s / self.tr / self.exp / self.bs / f"{self.bs}.csv")
                         for s in ["sched0","sched1","sched2"]}
        self._i = 0
        self.fx = FeatureBox(window_size=50)

    def set_scheduler(self, sched: str):  # called by sim executor
        if sched in self._streams: self._sched = sched

    async def run(self):
        while True:
            df = self._streams[self._sched]
            row = df.iloc[self._i]
            self.fx.push(row.get("dl_brate", row.get("tx_brate downlink [Mbps]", 0)),
                         row.get("ul_brate", row.get("rx_brate uplink [Mbps]", 0)),
                         row.get("dl_bler", 0.05),
                         int(row.get("nof_ue", row.get("ues", 10))))
            feats = FeatureBS(ts=now_s(), bs=self.bs, vec=self.fx.emit(), cluster=None)
            await self.bus.publish("feature.bs", feats)
            self._i = (self._i + 1) % len(df)
            await asyncio.sleep(self.tick)
