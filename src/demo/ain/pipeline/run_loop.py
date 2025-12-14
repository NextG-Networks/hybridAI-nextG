import asyncio
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from ain.agents.actor import run as actor_run
from ain.agents.observer import run as observer_run
from ain.agents.predictor import run as predictor_run
from ain.agents.proposer import run as proposer_run
from ain.brain.llm_reasoner import run as llm_reasoner_run
from ain.bus.mem import MemBus
from ain.pipeline.offline_demo import make_series


formatter = "<cyan>{time:YYYY-MM-DD HH:mm:ss.SSS}</cyan> | <level>{level}</level> | <level>{message}</level>"


@dataclass
class Config:
    win: int = 128
    model_path: str = "src/models/minirocket.joblib"
    target_kpi: str = "latency_ms"
    xapp_host: str = "0.0.0.0"
    xapp_port: int = 5000


async def kpi_stream():
    for datapoint in make_series():
        yield datapoint


async def _async_main(config: Config = None):
    logger.remove()
    logger.add(lambda m: print(m, end=""), format=formatter)
    
    if config is None:
        config = Config()
    
    bus = MemBus()
    
    # Initialize xApp TCP server (compatible with your dummy server)
    try:
        await bus.init_xapp_server(host=config.xapp_host, port=config.xapp_port)
        logger.info(f"xApp TCP server started on {config.xapp_host}:{config.xapp_port}")
    except Exception as e:
        logger.error(f"Failed to start xApp TCP server: {e}")
        raise

    # Create all AI system tasks
    ai_tasks = [
        asyncio.create_task(
            observer_run(
                bus,
                kpi_stream(),
                model_path=config.model_path,
                win=config.win,
            ),
            name="observer"
        ),
        asyncio.create_task(proposer_run(bus), name="proposer"),
        asyncio.create_task(predictor_run(bus), name="predictor"),
        asyncio.create_task(actor_run(bus), name="actor"),
        asyncio.create_task(
            llm_reasoner_run(bus, target_kpi=config.target_kpi), 
            name="llm_reasoner"
        ),
    ]
    
    # Add xApp server task
    xapp_task = asyncio.create_task(xapp_server_task(bus), name="xapp_server")
    
    all_tasks = ai_tasks + [xapp_task]
    
    try:
        logger.info("Starting hybrid AI system with xApp integration...")
        await asyncio.gather(*all_tasks)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise
    finally:
        # Cleanup
        logger.info("Cleaning up...")
        
        # Cancel all tasks
        for task in all_tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete cancellation
        await asyncio.gather(*all_tasks, return_exceptions=True)
        
        # Stop xApp server
        await bus.stop_xapp_server()
        
        logger.info("Shutdown complete")


async def xapp_server_task(bus: MemBus):
    """Keep xApp TCP server running and handle server-related tasks."""
    try:
        logger.info("xApp server task started")
        while True:
            # Keep the server alive and potentially handle periodic tasks
            await asyncio.sleep(1)
            
            # You could add periodic health checks, metrics collection, etc. here
            # For example:
            # if bus.xapp_server and hasattr(bus.xapp_server, 'clients'):
            #     client_count = len(bus.xapp_server.clients)
            #     if client_count > 0:
            #         logger.debug(f"Active xApp clients: {client_count}")
            
    except asyncio.CancelledError:
        logger.info("xApp server task cancelled")
        raise
    except Exception as e:
        logger.error(f"xApp server task error: {e}")
        raise


def main():
    """Main entry point."""
    config = Config()
    
    try:
        asyncio.run(_async_main(config))
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.error(f"Application failed: {e}")
        raise


if __name__ == "__main__":
    main()
