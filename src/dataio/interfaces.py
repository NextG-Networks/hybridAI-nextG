from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class DataSource(ABC):
    """Produces normalized KPI rows and/or feature messages."""
    @abstractmethod
    async def run(self): ...

class ActionSink(ABC):
    """Executes approved intents (sim = emulate; prod = call controller)."""
    @abstractmethod
    async def apply(self, intent: Dict[str, Any]): ...
