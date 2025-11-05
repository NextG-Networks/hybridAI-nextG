"""
proposer/mutate.py
Generate K small, safe mutations of a base playbook spec.
We operate on a normalized dict with fields: template_id, knobs, toggles.
"""

from __future__ import annotations
from typing import Dict, Any, List
import copy
import random

SAFE_HASH_PROFILES = ["A", "B", "C"]


def normalize_playbook(pb: Dict[str, Any]) -> Dict[str, Any]:
    # Expect dict with minimal fields; fill defaults
    pb = copy.deepcopy(pb) if pb else {}
    pb.setdefault("template_id", "pbA")
    pb.setdefault("knobs", {})
    pb.setdefault("toggles", {})
    k = pb["knobs"]
    t = pb["toggles"]
    k.setdefault("hash_profile", "A")
    k.setdefault("bfd", True)
    k.setdefault("hold_minutes", 10)
    t.setdefault("postcheck_strict", True)
    return pb


def mutate_once(pb: Dict[str, Any]) -> Dict[str, Any]:
    pb = normalize_playbook(pb)
    m = copy.deepcopy(pb)
    knobs = m["knobs"]
    toggles = m["toggles"]
    op = random.choice(["hash", "bfd", "hold", "postcheck"])
    if op == "hash":
        cur = knobs["hash_profile"]
        choices = [x for x in SAFE_HASH_PROFILES if x != cur]
        knobs["hash_profile"] = random.choice(choices) if choices else cur
    elif op == "bfd":
        knobs["bfd"] = not knobs.get("bfd", True)
    elif op == "hold":
        hm = int(knobs.get("hold_minutes", 10))
        knobs["hold_minutes"] = max(5, min(20, hm + random.choice([-5, -2, 2, 5])))
    else:
        toggles["postcheck_strict"] = not toggles.get("postcheck_strict", True)
    return m


def generate(base: Dict[str, Any], k: int = 3) -> List[Dict[str, Any]]:
    base = normalize_playbook(base)
    out = [base]
    for _ in range(max(0, k - 1)):
        out.append(mutate_once(base))
    # deduplicate
    uniq = []
    seen = set()
    for x in out:
        key = (
            x["template_id"],
            tuple(sorted(x["knobs"].items())),
            tuple(sorted(x["toggles"].items())),
        )
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    return uniq
