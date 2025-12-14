from pydantic import BaseModel
from typing import Literal, List, Dict, Optional


class Deviation(BaseModel):
    type: Literal["deviation"] = "deviation"
    kpi: str  # e.g., "latency_ms"
    current: float
    target: Optional[float] = None
    scope: Dict[str, str]  # e.g., {"service":"demo","region":"A"}
    evidence_window: List[float] = []
    confidence: float = 1.0


class Expectation(BaseModel):
    kpi: str
    op: Literal["<=", ">=", "=="]
    value: float
    weight: float = 1.0


class Intent(BaseModel):
    intent_id: str
    version: int = 1
    scope: Dict[str, str]
    expectations: List[Expectation]
    status: Literal["new", "active", "fulfilled", "violated", "removed"] = "new"


class Plan(BaseModel):
    plan_id: str
    actions: List[Dict[str, float]]  # [{"reroute":1}] etc.
    predicted: Dict[str, float]  # {"latency_ms":9.4}
    cost: float
    risk: float
    resources: List[str] = []  # for conflict checks


class Estimate(BaseModel):
    plan_id: str
    horizon_s: int
    predicted: Dict[str, float]
    confidence: float


class Report(BaseModel):
    intent_id: str
    state: Literal["accepted", "in_progress", "fulfilled", "violated"]
    evidence: Dict[str, float]
