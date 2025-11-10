from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any
import random

# -----------------------------
# Global configuration (PoC defaults)
# -----------------------------

PLAYBOOK_K = 3         # actions per playbook
CANDIDATE_N = 5        # number of candidate playbooks per decision
COOLDOWN_STEPS = 3     # cooldown per (type, scope, entity)

# -----------------------------
# Action definitions
# -----------------------------

ActionType = str   # {"SCHEDULER_POLICY","MCS_CAP","PRB_WEIGHT","SLICE_QOS","REPORTING"}
ScopeType = str    # {"CELL","UE","SLICE"}

@dataclass(frozen=True)
class ControlAction:
    type: ActionType
    scope: ScopeType
    cell_id: Optional[str] = None
    ue_id: Optional[str] = None
    slice_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        out = {"type": self.type, "scope": self.scope, "params": self.params}
        if self.cell_id is not None:
            out["cell_id"] = self.cell_id
        if self.ue_id is not None:
            out["ue_id"] = self.ue_id
        if self.slice_id is not None:
            out["slice_id"] = self.slice_id
        return out

@dataclass
class Playbook:
    actions: List[ControlAction]
    def to_json(self) -> List[Dict[str, Any]]:
        return [a.to_json() for a in self.actions]

# Small demo grids (extend as needed)
SCHEDULER_POLICIES = ["PF", "RR", "MAX_THROUGHPUT"]
MCS_DL_MAX_GRID = [14, 18, 22]
PRB_WEIGHT_GRID = [0.8, 1.0, 1.2]
SLICE_WEIGHT_GRID = [0.8, 1.0, 1.2]

@dataclass
class ActionSpace:
    cells: List[str]
    slices: List[str]
    def all_atomic_actions(self) -> List[ControlAction]:
        acts: List[ControlAction] = []
        for c in self.cells:
            for pol in SCHEDULER_POLICIES:
                acts.append(ControlAction("SCHEDULER_POLICY", "CELL", cell_id=c, params={"policy": pol}))
            for m in MCS_DL_MAX_GRID:
                acts.append(ControlAction("MCS_CAP", "CELL", cell_id=c, params={"dl_mcs_max": m}))
        for s in self.slices:
            for w in PRB_WEIGHT_GRID:
                acts.append(ControlAction("PRB_WEIGHT", "SLICE", slice_id=s, params={"weight": w}))
            for w in SLICE_WEIGHT_GRID:
                acts.append(ControlAction("SLICE_QOS", "SLICE", slice_id=s, params={"weight": w}))
        # Include a NOOP-like action
        acts.append(ControlAction("REPORTING", "CELL", params={"noop": True}))
        return acts

# Conflict and cooldown helpers
def conflict(a: ControlAction, b: ControlAction) -> bool:
    if a.type == "REPORTING" or b.type == "REPORTING":
        return False
    if a.type == b.type and a.scope == b.scope:
        if a.scope == "CELL" and a.cell_id == b.cell_id:
            return True
        if a.scope == "SLICE" and a.slice_id == b.slice_id:
            return True
        if a.scope == "UE" and a.ue_id == b.ue_id:
            return True
    return False

def cooldown_key(a: ControlAction) -> Tuple[str,str,str]:
    if a.scope == "CELL":
        ent = a.cell_id or "GLOBAL"
    elif a.scope == "SLICE":
        ent = a.slice_id or "GLOBAL"
    else:
        ent = a.ue_id or "GLOBAL"
    return (a.type, a.scope, ent)

def violates_cooldown(a: ControlAction, cooldown_clock: Dict[Tuple[str,str,str], int]) -> bool:
    return cooldown_clock.get(cooldown_key(a), 0) > 0

# -----------------------------
# Cache of good playbooks
# -----------------------------

class CacheLibrary:
    def __init__(self, max_per_key=20):
        self.max_per_key = max_per_key
        self.store: Dict[str, List[Tuple[Playbook, float]]] = {}

    def key(self, intent_meta: Dict[str,Any]) -> str:
        scope = intent_meta.get("scope","GLOBAL")
        intent = intent_meta.get("intent","LATENCY_P95")
        return f"{intent}:{scope}"

    def add(self, intent_meta: Dict[str,Any], playbook: Playbook, score: float):
        k = self.key(intent_meta)
        arr = self.store.setdefault(k, [])
        arr.append((playbook, score))
        arr.sort(key=lambda x: x[1], reverse=True)
        if len(arr) > self.max_per_key:
            arr[:] = arr[:self.max_per_key]

    def sample(self, intent_meta: Dict[str,Any], m=2) -> List[Playbook]:
        k = self.key(intent_meta)
        arr = self.store.get(k, [])
        if not arr:
            return []
        take = min(m, len(arr))
        return [pb for (pb, _) in random.sample(arr, take)]

# -----------------------------
# Proposer-side sampler
# -----------------------------

class ProposerSampler:
    @staticmethod
    def sample_playbooks(action_space: ActionSpace, N=CANDIDATE_N, K=PLAYBOOK_K,
                         cooldown_clock: Optional[Dict[Tuple[str,str,str], int]] = None,
                         cache: Optional[CacheLibrary] = None,
                         intent_meta: Optional[Dict[str,Any]] = None,
                         epsilon: float = 0.1) -> List[Playbook]:
        cooldown_clock = cooldown_clock or {}
        seeds = cache.sample(intent_meta, m=min(2, N)) if cache else []
        playbooks: List[Playbook] = []

        def random_playbook():
            actions = []
            all_acts = action_space.all_atomic_actions()
            tries = 0
            while len(actions) < K and tries < 50:
                a = random.choice(all_acts)
                if violates_cooldown(a, cooldown_clock):
                    tries += 1; continue
                if any(conflict(a, b) for b in actions):
                    tries += 1; continue
                actions.append(a)
            if len(actions) < K:
                actions += [ControlAction("REPORTING","CELL",params={"noop":True})] * (K - len(actions))
            return Playbook(actions)

        # Seeds (with mutation probability)
        for s in seeds:
            if random.random() < epsilon:
                pb = ProposerSampler.mutate_playbook(s, action_space, cooldown_clock)
            else:
                pb = s
            playbooks.append(pb)

        # Fill to N
        while len(playbooks) < N:
            playbooks.append(random_playbook())
        return playbooks

    @staticmethod
    def mutate_playbook(pb: Playbook, action_space: ActionSpace,
                        cooldown_clock: Dict[Tuple[str,str,str], int]) -> Playbook:
        idx = random.randrange(len(pb.actions))
        new_actions = pb.actions.copy()
        all_acts = action_space.all_atomic_actions()
        for _ in range(20):
            cand = random.choice(all_acts)
            if violates_cooldown(cand, cooldown_clock):
                continue
            tmp = new_actions.copy()
            tmp[idx] = cand
            if any(conflict(tmp[i], tmp[j]) for i in range(len(tmp)) for j in range(i+1,len(tmp))):
                continue
            new_actions = tmp
            break
        return Playbook(new_actions)
