import asyncio
from .observer_agent import RLObserverAgent
from .reasoner_agent import ReasonerAgent
from .proposer_agent import ProposerAgent
from .predictor_agent import PredictorAgent
from .actor_agent import ActorAgent
from .utils import make_msg
from ain.bus.mem import MemBus

async def run_all(intent, action_space, predictor, actor, source):
    bus = MemBus()

    # --- Pick KPI source ---
    if source == "fake":
        from ain.RL_demo.fake_kpi import fake_kpi_producer
        asyncio.create_task(fake_kpi_producer(bus))
    #else:
        #from ..loop.real_adapter import ran_adapter
        #asyncio.create_task(ran_adapter(bus))

    # --- Agents ---
    obs = RLObserverAgent(bus, intent=intent)
    rsn = ReasonerAgent(bus, intent=intent)
    prop = ProposerAgent(bus, action_space)
    pred = PredictorAgent(bus, predictor)
    act = ActorAgent(bus, actor)

    await asyncio.gather(obs.run(), rsn.run(), prop.run(), pred.run(), act.run())
