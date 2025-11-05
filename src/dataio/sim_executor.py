# (DEV)
from src.dataio.sim_source import SimSource


class SimExecutor(ActionSink):
    def __init__(self, sim_source: SimSource):
        self.sim_source = sim_source

    async def apply(self, intent):
        a = intent["action"]
        if a["type"] == "ChangeScheduler":
            self.sim_source.set_scheduler(a["policy"])
