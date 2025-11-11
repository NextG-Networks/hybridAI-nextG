
#!/usr/bin/env python3
"""
demo_cli.py — End-to-end command-line demo wiring:
Fake KPI stream -> Reasoner -> Proposer -> Predictor/Observer (RL) -> Actor JSON output

Run:
  python demo_cli.py --steps 60 --target 40 --save-every 10

It will also try to spawn the fake KPI generator in the background.
"""

from __future__ import annotations
import argparse
import os
import sys
import time
import json
import signal
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from ain.loop.actor import Actor, ControlAction as AControlAction, Playbook as APlaybook

def to_actor_playbook(pb) -> APlaybook:
    """Convert proposer.Playbook -> actor.Playbook"""
    acts = []
    for a in pb.actions:
        acts.append(AControlAction(
            type=a.type,
            scope=a.scope,
            cell_id=getattr(a, "cell_id", None),
            ue_id=getattr(a, "ue_id", None),
            slice_id=getattr(a, "slice_id", None),
            params=dict(getattr(a, "params", {}) or {}),
        ))
    return APlaybook(actions=acts)



# --- make local files importable under the "ain.loop" namespace expected by predictor.py ---
import types
import importlib

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

# Map local modules into "ain.loop.*" so predictor.py's imports work
def _alias_local_module(pkg_name: str, module_obj):
    pkg_parts = pkg_name.split(".")
    # Ensure parent packages exist in sys.modules
    accum = []
    parent = None
    for i, part in enumerate(pkg_parts):
        accum.append(part)
        name = ".".join(accum)
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
        if parent is not None:
            setattr(sys.modules[".".join(accum[:-1])], part, sys.modules[name])
        parent = sys.modules[name]
    # Register the module object at the leaf
    sys.modules[pkg_name] = module_obj
    # Also attach to its parent package attribute
    parent_name = ".".join(pkg_parts[:-1])
    if parent_name:
        setattr(sys.modules[parent_name], pkg_parts[-1], module_obj)

# Import local modules
import ain.loop.proposer as _proposer_local
_alias_local_module("ain.loop.proposer", _proposer_local)

import ain.loop.predictor as _predictor_local
import ain.loop.observer_rl as _observer_local
import ain.loop.actor as _actor_local

from ain.loop.proposer import (
    ActionSpace, ProposerSampler, CacheLibrary,
    PLAYBOOK_K, CANDIDATE_N, COOLDOWN_STEPS, cooldown_key,
)
from ain.loop.observer_rl import Intent, RLObserver
from ain.loop.predictor import SlateDQNPredictor
from ain.loop.actor import Actor

# Optional: lightweight "reasoner" that would normally call GPT; here we use rules or a stub
from ain.loop.reasoner import Reasoner, ReasonerConfig

# ----------------------------
# Demo logic
# ----------------------------

def spawn_fake_kpi(interval_sec: int = 2) -> subprocess.Popen:
    """Start fake_kpi.py as a background process (if not already running)."""
    # If a process is already writing the file, we just start another (harmless), or skip by env flag.
    env = os.environ.copy()
    cmd = [sys.executable, str(THIS_DIR / "fake_kpi.py")]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=60, help="Max steps for one intent lifecycle")
    ap.add_argument("--target", type=float, default=40.0, help="Intent metric target (delay_p95_ms)")
    ap.add_argument("--success-streak", type=int, default=5, help="Consecutive hits to consider intent achieved")
    ap.add_argument("--save-every", type=int, default=10, help="Save actor JSON every N steps")
    ap.add_argument("--no-spawn-kpi", action="store_true", help="Do not spawn the fake KPI generator")
    ap.add_argument("--out-dir", type=str, default="configs", help="Where to save playbook JSONs")
    args = ap.parse_args()

    # 1) (Optional) Start KPI stream
    kpi_proc = None
    if not args.no_spawn_kpi:
        kpi_proc = spawn_fake_kpi()

    try:
        # 2) Reasoner generates an intent from SLO inputs (here: fixed + stub)
        reasoner = Reasoner(ReasonerConfig())
        intent_msg = reasoner.create_intent(slo={"objective": "REDUCE_LATENCY", "metric": "delay_p95_ms", "target": args.target})
        # Map to RLObserver.Intent
        intent = Intent(
            type=intent_msg.get("type", "REDUCE_LATENCY"),
            metric=intent_msg.get("metric", "delay_p95_ms"),
            target=float(intent_msg.get("target", args.target)),
            direction=intent_msg.get("direction", "lower_better"),
            action_cost=float(intent_msg.get("action_cost", 0.01)),
            reward_clip=float(intent_msg.get("reward_clip", 2.0)),
        )

        print(f"[Reasoner] Intent: {intent}")

        # 3) Create ActionSpace/Cache/Predictor/Observer/Actor
        cells = ["CELL_001", "CELL_002"]
        slices = ["SLICE_A", "SLICE_B"]
        action_space = ActionSpace(cells=cells, slices=slices)
        cache = CacheLibrary(max_per_key=20)
        predictor = SlateDQNPredictor(action_space, feat_dim=len(RLObserver(None, intent).features), seed=0)
        observer = RLObserver(predictor, intent=intent, kpi_file="fake_kpi_stream.json", window=12)
        actor = Actor(out_dir=args.out_dir)

        # Cooldown clock (mirrors pseudo_demo behavior)
        cooldown_clock: Dict = {}

        # 4) Run one intent lifecycle loop
        last_playbook = None
        success_streak = 0
        step = 0
        print("[Demo] Waiting for KPI stream...")
        while step < args.steps:
            state = observer.step(last_playbook)
            if state is None:
                time.sleep(0.5)
                continue

            # Check success
            latest = observer.last_kpi_raw or {}
            cell = (latest or {}).get("CellMetrics", {})
            curr = float(cell.get(intent.metric, 0.0))
            hit = (curr <= intent.target) if intent.direction == "lower_better" else (curr >= intent.target)
            success_streak = success_streak + 1 if hit else 0

            # --- Proposer: sample candidate playbooks (using cache & cooldown) ---
            intent_meta = {"intent": intent.type, "scope": "CELL:CELL_001"}
            candidates = ProposerSampler.sample_playbooks(
                action_space, N=CANDIDATE_N, K=PLAYBOOK_K,
                cooldown_clock=cooldown_clock,
                cache=cache, intent_meta=intent_meta, epsilon=predictor.epsilon()
            )

            # --- Predictor: score and pick best ---
            scored = predictor.score_playbooks(state, candidates)
            scored.sort(key=lambda x: x[1], reverse=True)
            best_pb, best_q = scored[0]

            # Apply cooldown bookkeeping
            for a in best_pb.actions:
                ck = cooldown_key(a)
                cooldown_clock[ck] = max(cooldown_clock.get(ck, 0), COOLDOWN_STEPS)
            # Decrement cooldowns
            for k in list(cooldown_clock.keys()):
                cooldown_clock[k] -= 1
                if cooldown_clock[k] <= 0:
                    cooldown_clock.pop(k, None)

            # Cache best
            cache.add(intent_meta, best_pb, best_q)

            # --- Actor: log + optionally save JSON ---
            print(f"[t={step:03d}] metric={intent.metric}={curr:.2f}  hit={hit} streak={success_streak}  "
                  f"eps={predictor.epsilon():.3f}  q={best_q:.3f}")
            for i, a in enumerate(best_pb.actions):
                print(f"   • A{i+1}: {a.type} {a.scope} cell={a.cell_id} slice={a.slice_id} params={a.params}")

            if (step % max(1, args.save_every) == 0) or (success_streak >= args.success_streak):
                actor_pb = to_actor_playbook(best_pb)
                payload = actor.make_payload(
                    playbook=actor_pb,
                    intent={"type": intent.type, "metric": intent.metric, "target": intent.target},
                    extra_meta={"q_score": best_q, "step": step}
                )
                out_path = actor.save_payload(payload)
                print(f"   -> Saved JSON to: {out_path}")

            # Prepare for next loop
            last_playbook = best_pb
            predictor.steps += 1
            step += 1

            # Stop when success streak achieved
            if success_streak >= args.success_streak:
                print(f"[Demo] Intent achieved for {success_streak} consecutive readings. Stopping.")
                break

            # Sleep a bit to sync with KPI cadence
            time.sleep(0.5)

    finally:
        if kpi_proc is not None:
            try:
                kpi_proc.send_signal(signal.SIGINT)
            except Exception:
                pass

if __name__ == "__main__":
    main()
