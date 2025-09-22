import asyncio
from loguru import logger
from ain.bus.mem import MemBus
from ain.agents.observer import run as observer_run
from ain.agents.predictor import run as predictor_run
from ain.agents.proposer import run as proposer_run
from ain.agents.actor import run as actor_run
from ain.brain import reasoning
from ain.pipeline.offline_demo import make_series

ROLE_COLORS = {
    "Observer": "<blue>",
    "Predictor": "<cyan>",
    "Proposer": "<magenta>",
    "Actor": "<yellow>",
    "Reasoning": "<green>",
    "Assurance": "<white>",
}

def formatter(record):
    # Escape braces so Loguru doesn’t parse them
    msg = record["message"].replace("{", "{{").replace("}", "}}")
    name = record["name"].lower()

    role = None
    for key in ROLE_COLORS:
        if key in name or key in msg.lower():
            role = key
            break

    open_tag = ROLE_COLORS.get(role, "<white>")
    close_tag = open_tag.replace("<", "</")  # e.g. "<blue>" -> "</blue>"
    t = record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    return f"<dim>{t}</dim> | {open_tag}{msg}{close_tag}\n"

async def kpi_stream(period=0.01, n=9000, seed=2):
    x, _ = make_series(n=n, seed=seed)
    for v in x.values:
        yield float(v)
        await asyncio.sleep(period)


async def _async_main():
    logger.remove()
    logger.add(lambda m: print(m, end=""), format=formatter)
    bus = MemBus()
    tasks = [
        asyncio.create_task(
            observer_run(
                bus, kpi_stream(), model_path="models/minirocket.joblib", win=128
            )
        ),
        asyncio.create_task(proposer_run(bus)),
        asyncio.create_task(predictor_run(bus)),
        asyncio.create_task(actor_run(bus)),
        asyncio.create_task(
            reasoning.run(
                bus, target_kpi="latency_ms", target_value=10.0, stable_windows=5
            )
        ),
    ]
    await asyncio.gather(*tasks)


def main():
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
