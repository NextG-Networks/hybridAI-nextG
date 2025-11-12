from __future__ import annotations
import argparse
import os
import sys
import time
import json
import signal
import subprocess
from pathlib import Path
from typing import Dict, Any
import types

# ---- Make local files importable under the "ain.loop" namespace expected by predictor.py
THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

def _alias_local_module(pkg_name: str, module_obj):
    pkg_parts = pkg_name.split(".")
    parent = None
    for i in range(len(pkg_parts)):
        name = ".".join(pkg_parts[: i + 1])
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
        if i > 0:
            setattr(sys.modules[".".join(pkg_parts[:i])], pkg_parts[i], sys.modules[name])
        parent = sys.modules[name]
    sys.modules[pkg_name] = module_obj
    setattr(sys.modules[".".join(pkg_parts[:-1])], pkg_parts[-1], module_obj)

# Local modules
import ain.loop.proposer as _proposer_local
_alias_local_module("ain.loop.proposer", _proposer_local)

# Regular imports (now resolvable)
from ain.loop.proposer import (
    ActionSpace, ProposerSampler, CacheLibrary,
    PLAYBOOK_K, CANDIDATE_N, COOLDOWN_STEPS, cooldown_key,
)
from ain.loop.observer_rl import Intent, RLObserver
from ain.loop.predictor import SlateDQNPredictor
from ain.loop.actor import Actor, ControlAction as AControlAction, Playbook as APlaybook

# New Reasoner adapters
from ain.brain.llm_reasoner import normalize_deviation, to_proposer_meta, to_rl_intent
from ain.brain.openai_client import reason_from_deviation, OpenAIError, fallback_intent_for_deviation


# ----------------------------
# Helpers
# ----------------------------

def spawn_fake_kpi() -> subprocess.Popen:
    """Start fake_kpi.py as a background process."""
    cmd = [sys.executable, str(THIS_DIR / "fake_kpi.py")]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc

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

def deviation_from_kpi(kpi: dict, default_target: float, metric: str = "delay_p95_ms") -> dict:
    c = (kpi or {}).get("CellMetrics", {}) or {}
    value = float(c.get(metric, 0.0))
    direction = "lower_better" if ("delay" in metric or "latency" in metric) else "higher_better"
    return {
        "source": "minirocket",
        "metric": metric,
        "value": value,
        "target": default_target,
        "direction": direction,
        "severity": "medium",
        "scope": {
            "cell_id": c.get("cell_id") or "CELL_001",
            "region": "A",
            "service": "demo",
            "tenancy": "prod",
        },
        "evidence_ref": "telemetry://window/A",
    }

def _parse_cell_from_scope(scope_str: str | None) -> str | None:
    if not scope_str:
        return None
    if scope_str.startswith("CELL:"):
        return scope_str.split(":", 1)[1]
    return None

def _parse_slice_from_scope(scope_str: str | None) -> str | None:
    if not scope_str:
        return None
    if scope_str.startswith("SLICE:"):
        return scope_str.split(":", 1)[1]
    return None

def to_actor_playbook(pb, scope_hint: str | None = None) -> APlaybook:
    """Convert proposer.Playbook -> actor.Playbook, filling missing IDs from scope_hint."""
    default_cell = _parse_cell_from_scope(scope_hint) or "CELL_001"
    default_slice = _parse_slice_from_scope(scope_hint)

    acts = []
    for a in pb.actions:
        cell_id = getattr(a, "cell_id", None)
        slice_id = getattr(a, "slice_id", None)

        # If an action targets CELL but has no cell_id (e.g., REPORTING), use scope hint.
        if a.scope == "CELL" and not cell_id:
            cell_id = default_cell

        # If an action targets SLICE but has no slice_id, try scope hint (optional).
        if a.scope == "SLICE" and not slice_id and default_slice:
            slice_id = default_slice

        acts.append(AControlAction(
            type=a.type,
            scope=a.scope,
            cell_id=cell_id,
            ue_id=getattr(a, "ue_id", None),
            slice_id=slice_id,
            params=dict(getattr(a, "params", {}) or {}),
        ))
    return APlaybook(actions=acts)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=30, help="Max steps for one intent lifecycle")
    ap.add_argument("--target", type=float, default=40.0, help="Default latency SLO target (ms) for delay_p95_ms")
    ap.add_argument("--success-streak", type=int, default=4, help="Consecutive hits to consider intent achieved")
    ap.add_argument("--save-every", type=int, default=10, help="Save actor JSON every N steps")
    ap.add_argument("--no-spawn-kpi", action="store_true", help="Do not spawn the fake KPI generator")
    ap.add_argument("--out-dir", type=str, default="configs", help="Where to save playbook JSONs")
    ap.add_argument("--reasoner", choices=["llm","fallback"], default="llm", help="Use LLM or deterministic fallback")
    ap.add_argument("--ext-metric", type=str, default=None, help="External SLO metric (e.g., thr_dl_bps)")
    ap.add_argument("--ext-target", type=float, default=None, help="External SLO target value")
    ap.add_argument("--offline-model", type=str, default=None, help="Path to trained Q model (e.g., models/qnet_offline.pt)")
    args = ap.parse_args()

    # Optional KPI generator
    kpi_proc = None
    if not args.no_spawn_kpi:
        kpi_proc = spawn_fake_kpi()

    try:
        # Build components (temporary intent so RLObserver can start reading)
        cells = ["CELL_001", "CELL_002"]
        slices = ["SLICE_A", "SLICE_B"]
        action_space = ActionSpace(cells=cells, slices=slices)
        cache = CacheLibrary(max_per_key=20)

        tmp_intent = Intent(type="REDUCE_LATENCY", metric="delay_p95_ms", target=args.target)
        predictor = SlateDQNPredictor(action_space, feat_dim=len(RLObserver(None, tmp_intent).features), seed=0)
        if args.offline_model:
            try:
                predictor.load_offline(args.offline_model)
                print(f"[Predictor] Loaded offline weights from {args.offline_model}")
            except Exception as e:
                print(f"[Predictor] Could not load offline weights ({e}). Using fresh model.")
        observer = RLObserver(predictor, intent=tmp_intent, kpi_file="fake_kpi_stream.json", window=12)
        actor = Actor(out_dir=args.out_dir)

        cooldown_clock: Dict = {}
        last_playbook = None
        success_streak = 0
        step = 0
        net_intent = None
        intent_meta = {"intent":"LATENCY_P95", "scope":"GLOBAL"}  # will be replaced
        print("[Demo] Waiting for KPI stream...")

        while step < args.steps:
            state = observer.step(last_playbook)
            if state is None:
                time.sleep(0.5)
                continue

            latest = observer.last_kpi_raw or {}
            cell = (latest or {}).get("CellMetrics", {})
            curr = float(cell.get(observer.intent.metric, 0.0))
            hit = (curr <= observer.intent.target) if observer.intent.direction == "lower_better" else (curr >= observer.intent.target)
            success_streak = success_streak + 1 if hit else 0

            # Bootstrap intent on step==0 (or you could refresh on any condition)
            if step == 0:
                if args.ext_metric and args.ext_target is not None:
                    dev_raw = {
                        "source": "external_slo",
                        "metric": args.ext_metric,
                        "value": 0.0,
                        "target": float(args.ext_target),
                        "direction": "lower_better" if ("delay" in args.ext_metric or "latency" in args.ext_metric) else "higher_better",
                        "severity": "high",
                        "scope": {"region":"A","service":"demo","tenancy":"prod"},
                    }
                else:
                    dev_raw = deviation_from_kpi(latest, default_target=args.target, metric="delay_p95_ms")

                dev = normalize_deviation(dev_raw)
                try:
                    net_intent = reason_from_deviation(dev) if args.reasoner == "llm" else None
                    if net_intent is None:
                        raise OpenAIError("LLM disabled")
                except OpenAIError:
                    net_intent = fallback_intent_for_deviation(dev)

                intent_meta = to_proposer_meta(net_intent)
                rl_cfg = to_rl_intent(net_intent)

                # Swap RL intent so rewards align
                new_intent = Intent(
                    type=rl_cfg["type"],
                    metric=rl_cfg["metric"],
                    target=float(rl_cfg["target"]),
                    direction=rl_cfg["direction"],
                    action_cost=float(rl_cfg.get("action_cost", 0.01)),
                    reward_clip=float(rl_cfg.get("reward_clip", 2.0)),
                )
                observer.intent = new_intent
                print(f"[Reasoner] Intent set from deviation: {observer.intent}  (scope={intent_meta['scope']})")

            # Proposer candidates
            candidates = ProposerSampler.sample_playbooks(
                action_space, N=CANDIDATE_N, K=PLAYBOOK_K,
                cooldown_clock=cooldown_clock,
                cache=cache, 
                intent_meta=intent_meta, 
                epsilon=predictor.epsilon()
            )

            # Predictor scoring
            scored = predictor.score_playbooks(state, candidates)
            scored.sort(key=lambda x: x[1], reverse=True)
            best_pb, best_q = scored[0]

            # Cooldown bookkeeping
            for a in best_pb.actions:
                ck = cooldown_key(a)
                cooldown_clock[ck] = max(cooldown_clock.get(ck, 0), COOLDOWN_STEPS)
            for k in list(cooldown_clock.keys()):
                cooldown_clock[k] -= 1
                if cooldown_clock[k] <= 0:
                    cooldown_clock.pop(k, None)

            # Cache
            cache.add(intent_meta, best_pb, best_q)

            print(f"[t={step:03d}] metric={observer.intent.metric}={curr:.2f}  hit={hit} streak={success_streak}  "
                  f"eps={predictor.epsilon():.3f}  q={best_q:.3f}")
            for i, a in enumerate(best_pb.actions):
                print(f"   • A{i+1}: {a.type} {a.scope} cell={a.cell_id} slice={a.slice_id} params={a.params}")

            # Save JSON per cadence or on success
            if (step % max(1, args.save_every) == 0) or (success_streak >= args.success_streak):
                actor_pb = to_actor_playbook(best_pb)
                payload = actor.make_payload(
                    playbook=actor_pb,
                    intent={"type": observer.intent.type, "metric": observer.intent.metric, "target": observer.intent.target},
                    extra_meta={"q_score": best_q, "step": step, "scope": intent_meta.get("scope","GLOBAL")}
                )
                out_path = actor.save_payload(payload)
                print(f"   -> Saved JSON to: {out_path}")

            last_playbook = best_pb
            predictor.steps += 1
            step += 1

            if success_streak >= args.success_streak:
                print(f"[Demo] Intent achieved for {success_streak} consecutive readings. Stopping.")
                break

            time.sleep(0.5)

    finally:
        if kpi_proc is not None:
            try:
                print("[Demo] Terminating fake KPI generator...")
                kpi_proc.terminate()
                kpi_proc.wait(timeout=3)
            except Exception:
                pass


if __name__ == "__main__":
    main()
