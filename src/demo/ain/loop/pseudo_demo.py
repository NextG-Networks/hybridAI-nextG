from __future__ import annotations
from typing import List, Tuple, Dict, Any
import numpy as np

from ain.loop.proposer import (
    ActionSpace, ProposerSampler, CacheLibrary,
    PLAYBOOK_K, CANDIDATE_N, COOLDOWN_STEPS,
)
from ain.loop.predictor import SlateDQNPredictor

# KPI state config
W = 12
STATE_FEATS = [
    "thr_dl_bps","thr_ul_bps","bler_dl","bler_ul","cqi_avg","mcs_dl_avg","mcs_ul_avg",
    "active_ue_count","prb_used_dl_ratio","delay_p95_ms"
]
FEAT_DIM = len(STATE_FEATS)

SLO_P95_MS = 40.0

# -----------------------------
# Pseudo KPI environment
# -----------------------------

class PseudoEnv:
    def __init__(self, cells: List[str], slices: List[str], seed=0):
        np.random.seed(seed)
        self.cells = cells
        self.slices = slices
        self.t = 0
        self.load = 0.6
        self.latency_ms = 60.0
        self.cooldown_clock: Dict = {}
        self.history = []
        for _ in range(W):
            self.history.append(self._gen_features())

    def step(self, playbook):
        self.t += 1
        for k in list(self.cooldown_clock.keys()):
            self.cooldown_clock[k] = max(0, self.cooldown_clock[k]-1)
            if self.cooldown_clock[k] == 0:
                self.cooldown_clock.pop(k, None)

        eff = self._playbook_effect(playbook)
        noise = np.random.normal(scale=0.02)
        self.load = np.clip(self.load + noise - 0.05*eff["load_reduce"] + 0.03*eff["load_increase"], 0.0, 1.5)
        base = 20 + 80*self.load
        self.latency_ms = max(5.0, 0.7*self.latency_ms + 0.3*base - 2.0*eff["latency_reduce"] + 1.5*eff["latency_increase"])

        feats = self._gen_features()
        self.history.append(feats)
        if len(self.history) > W: self.history.pop(0)

        L_prev = self.history[-2][-1]
        L_now = self.history[-1][-1]
        dL = (L_prev - L_now) / max(L_prev, 1e-6)
        violation = 1.0 if L_now > SLO_P95_MS else 0.0
        action_cost = 0.01 * len(playbook.actions)
        reward = float(np.clip(1.0*dL - 1.0*violation - action_cost, -2.0, 2.0))
        done = False
        info = {"delay_p95_ms": L_now, "violation": violation}

        # naive cooldown marking (same as in proposer)
        from ain.loop.proposer import cooldown_key
        for a in playbook.actions:
            self.cooldown_clock[cooldown_key(a)] = max(self.cooldown_clock.get(cooldown_key(a), 0), COOLDOWN_STEPS)

        return np.array(self.history, dtype=np.float32), reward, done, info

    def current_state(self) -> np.ndarray:
        return np.array(self.history, dtype=np.float32)

    def _gen_features(self) -> np.ndarray:
        thr_dl = 50e6 * (1.2 - self.load) + np.random.normal(scale=2e6)
        thr_ul = 10e6 * (1.2 - self.load) + np.random.normal(scale=0.5e6)
        bler_dl = np.clip(0.05 + 0.2*self.load + np.random.normal(scale=0.01), 0, 1)
        bler_ul = np.clip(0.04 + 0.15*self.load + np.random.normal(scale=0.01), 0, 1)
        cqi = np.clip(12 - 6*self.load + np.random.normal(scale=0.5), 1, 15)
        mcs_dl = np.clip(25 - 10*self.load + np.random.normal(scale=0.8), 0, 28)
        mcs_ul = np.clip(20 - 8*self.load + np.random.normal(scale=0.8), 0, 28)
        ue_count = int(30 + 50*self.load + np.random.normal(scale=5))
        prb_ratio = np.clip(0.2 + 0.6*self.load + np.random.normal(scale=0.05), 0, 1)
        delay_p95 = self.latency_ms + np.random.normal(scale=1.0)

        feats = np.array([
            thr_dl/1e7, thr_ul/1e7, bler_dl, bler_ul, cqi, mcs_dl, mcs_ul,
            ue_count/100.0, prb_ratio, delay_p95/100.0
        ], dtype=np.float32)
        return feats

    def _playbook_effect(self, pb):
        reduce = increase = load_red = load_inc = 0.0
        for a in pb.actions:
            if a.type == "SCHEDULER_POLICY":
                pol = a.params.get("policy","PF")
                if pol == "PF": reduce += 1.0
                elif pol == "RR": increase += 0.5
                elif pol == "MAX_THROUGHPUT": increase += 0.2; load_red += 0.1
            elif a.type == "MCS_CAP":
                v = a.params.get("dl_mcs_max", 18)
                if v == 14: reduce += 0.6; load_red += 0.1
                elif v == 18: reduce += 0.3
                elif v == 22: increase += 0.1
            elif a.type == "PRB_WEIGHT":
                w = a.params.get("weight",1.0)
                if w > 1.0: reduce += 0.5; load_red += 0.1
                elif w < 1.0: increase += 0.2
            elif a.type == "SLICE_QOS":
                w = a.params.get("weight",1.0)
                if w > 1.0: reduce += 0.3
                elif w < 1.0: increase += 0.1
        return {"latency_reduce": reduce, "latency_increase": increase, "load_reduce": load_red, "load_increase": load_inc}

# -----------------------------
# Demo runner
# -----------------------------

def run_poc(steps=500, seed=0, log_every=25):
    cells = ["C1","C2"]
    slices = ["S1","S2"]
    action_space = ActionSpace(cells=cells, slices=slices)
    env = PseudoEnv(cells, slices, seed=seed)
    predictor = SlateDQNPredictor(action_space, feat_dim=FEAT_DIM, seed=seed)
    cache = CacheLibrary(max_per_key=20)
    intent_meta = {"intent":"LATENCY_P95", "scope":"CELL:C1"}
    rewards = []

    for t in range(steps):
        state = env.current_state()
        candidates = ProposerSampler.sample_playbooks(
            action_space, N=CANDIDATE_N, K=PLAYBOOK_K,
            cooldown_clock=env.cooldown_clock.copy(),
            cache=cache, intent_meta=intent_meta, epsilon=predictor.epsilon()
        )
        scored = predictor.score_playbooks(state, candidates)
        scored.sort(key=lambda x: x[1], reverse=True)
        best_pb, best_q = scored[0]
        next_state, reward, done, info = env.step(best_pb)
        rewards.append(reward)
        cache.add(intent_meta, best_pb, best_q)

        play_onehot = predictor.encode_playbook_onehot(best_pb)
        predictor.replay.push(state, play_onehot, reward, next_state, done)
        predictor.learn_step(FEAT_DIM)
        predictor.steps += 1

        if t % log_every == 0:
            avg_r = np.mean(rewards[-log_every:]) if len(rewards) >= log_every else np.mean(rewards)
            print(f"t={t:04d} eps={predictor.epsilon():.3f} avgR={avg_r:.3f} q={best_q:.3f} delay_p95*100={info['delay_p95_ms']:.2f}")

    return predictor, cache

def main():
    run_poc(steps=500, seed=0, log_every=25)

if __name__ == "__main__":
    main()
