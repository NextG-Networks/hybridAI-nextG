# (PROD stub)
class LiveSource(DataSource):
    def __init__(self, bus, live_cfg):
        self.bus = bus
        self.cfg = live_cfg

    async def run(self):
        # subscribe to real KPI bus (ns-3/xApp later) and publish FeatureBS messages
        # keep same feature pipeline as SimSource -> identical downstream behavior
        ...
