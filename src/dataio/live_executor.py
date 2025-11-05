# (PROD stub)
import httpx, asyncio


class LiveExecutor(ActionSink):
    def __init__(self, controller_url: str):
        self.url = controller_url

    async def apply(self, intent):
        # POST to controller API; handle retries & idempotency
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{self.url}/intent", json=intent)
