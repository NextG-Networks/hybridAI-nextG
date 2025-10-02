from pydantic import BaseModel, Field, validator
from typing import Literal, List, Dict, Optional
from datetime import datetime, timezone

LifecycleState = Literal[
    "draft", "active", "in_progress", "fulfilled", "violated", "removed"
]


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


class ModifyPatch(BaseModel):
    Expectations: Optional[List[Expectation]] = None
    scope: Optional[Dict[str, str]] = None
    resources: Optional[List[str]] = None


class Intent(BaseModel):
    intent_id: str
    version: int = 1

    scope: Dict[str, str]
    expectations: List[Expectation]

    status: LifecycleState = "draft"
    resources: List[str] = []

    def touch(self) -> None:
        """Refresh updated_at (call after any mutation)."""
        object.__setattr__(self, "updated_at", datetime.now(timezone.utc))

    def apply_patch(self, patch: ModifyPatch) -> "Intent":
        """Non-destructive convenience: returns a new Intent with patch applied."""
        data = self.dict()
        if patch.expectations is not None:
            data["expectations"] = [
                e.dict() if isinstance(e, BaseModel) else e for e in patch.expectations
            ]
        if patch.scope is not None:
            data["scope"] = patch.scope
        if patch.ttl_s is not None:
            data["ttl_s"] = patch.ttl_s
        if patch.resources is not None:
            data["resources"] = patch.resources
        data["version"] = self.version + 1
        data["updated_at"] = datetime.now(timezone.utc)
        # if someone patched a fulfilled/violated/removed intent, bounce it back to active
        if data.get("status") in ("fulfilled", "violated", "removed"):
            data["status"] = "active"
        return Intent(**data)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if self.ttl_s is None:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - self.created_at).total_seconds() > self.ttl_s

    # Simple, explicit FSM; callers should persist the returned copy
    def transition(self, new_state: LifecycleState) -> "Intent":
        allowed = {
            "draft": {"active", "removed"},
            "active": {"in_progress", "fulfilled", "violated", "removed"},
            "in_progress": {"fulfilled", "violated", "removed"},
            "fulfilled": {"active", "removed"},  # can reactivate if modified
            "violated": {"active", "removed"},  # can reactivate if modified
            "removed": set(),  # terminal in-store (usually deleted)
        }
        if new_state not in allowed[self.status]:
            # no-op if illegal transition; you may prefer raising ValueError
            return self
        data = self.dict()
        data["status"] = new_state
        data["updated_at"] = datetime.now(timezone.utc)
        return Intent(**data)


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
    evidence: Dict[str, float] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
