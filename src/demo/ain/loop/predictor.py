
from __future__ import annotations
from typing import List, Tuple, Dict, Any
import math
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from ain.loop.proposer import (
    Playbook, ControlAction, ActionSpace,
    PLAYBOOK_K, CANDIDATE_N,
)

from ain.loop.model_defs import SlateDQNetwork

# -----------------------------
# Config (can be tweaked)
# -----------------------------

GAMMA = 0.99 # Discount factor (closer to one means longterm learning, lower means short term learning)
LR = 1e-3
BATCH_SIZE = 64
REPLAY_CAP = 100000
TAU = 0.005 # Target network soft update rate
EPS_START = 0.2 # Exploration vs exploitation
EPS_END = 0.05
EPS_DECAY_STEPS = 20000

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu") # Use GPU if available

# -----------------------------
# Networks
# -----------------------------

# -----------------------------
# Replay Buffer
# -----------------------------

class ReplayBuffer: # This is where we store our previous experiences
    def __init__(self, capacity=REPLAY_CAP):
        self.capacity = capacity
        self.buf = []
        self.pos = 0
    def push(self, s, p, r, s2, d):
        data = (s, p, float(r), s2, bool(d))
        if len(self.buf) < self.capacity:
            self.buf.append(data)
        else:
            self.buf[self.pos] = data
        self.pos = (self.pos + 1) % self.capacity
    def sample(self, batch_size): # Makes sure we can sample a batch of experiences for learning
        idxs = np.random.choice(len(self.buf), batch_size, replace=False)
        batch = [self.buf[i] for i in idxs]
        s, p, r, s2, d = zip(*batch)
        return (
            np.array(s), 
            np.array(p), 
            np.array(r, dtype=np.float32), 
            np.array(s2), 
            np.array(d, dtype=np.float32)
        )
    def __len__(self):
        return len(self.buf)

# -----------------------------
# Predictor / Learner
# -----------------------------

class SlateDQNPredictor:
    def __init__(self, action_space: ActionSpace, feat_dim: int, seed=0):
        np.random.seed(seed); torch.manual_seed(seed)
        self.action_space = action_space
        self.cell_index = {c:i for i,c in enumerate(action_space.cells)}
        self.slice_index = {s:i for i,s in enumerate(action_space.slices)}

        cell_cap = len(self.cell_index)
        slice_cap = len(self.slice_index)

        self.model = SlateDQNetwork(feat_dim=feat_dim,
                                    cell_cap=cell_cap,
                                    slice_cap=slice_cap).to(DEVICE)
        self.target = SlateDQNetwork(feat_dim=feat_dim,
                                     cell_cap=cell_cap,
                                     slice_cap=slice_cap).to(DEVICE)
        self.target.load_state_dict(self.model.state_dict())
        self.optim = torch.optim.Adam(self.model.parameters(), lr=LR)
        self.replay = ReplayBuffer(capacity=REPLAY_CAP)
        self.steps = 0
        self.action_onehot_dim = self.model.input_dim_action_onehot


    # Encodes our actions intpo one-hot vectors and stacks them
    def _one_hot(self, idx: int, dim: int):
        v = np.zeros(dim, dtype=np.float32)
        if 0 <= idx < dim: v[idx] = 1.0
        return v

    def _encode_action_np(self, a: ControlAction) -> np.ndarray:
        type_map = {"SCHEDULER_POLICY":0, "MCS_CAP":1, "PRB_WEIGHT":2, "SLICE_QOS":3, "REPORTING":4}
        scope_map = {"CELL":0, "UE":1, "SLICE":2}
        vecs = []
        vecs.append(self._one_hot(type_map.get(a.type, 4), 5))
        vecs.append(self._one_hot(scope_map.get(a.scope, 0), 3))

        n_cells = len(self.cell_index)
        n_slices = len(self.slice_index)
        vecs.append(self._one_hot(self.cell_index.get(a.cell_id, -1), n_cells if n_cells > 0 else 1))
        vecs.append(self._one_hot(self.slice_index.get(a.slice_id, -1), n_slices if n_slices > 0 else 1))

        p = np.zeros(8, dtype=np.float32)
        if a.type == "SCHEDULER_POLICY":
            pol = a.params.get("policy","PF")
            pol_map = {"PF":0,"RR":1,"MAX_THROUGHPUT":2}
            p[pol_map.get(pol,0)] = 1.0
        elif a.type == "MCS_CAP":
            v = a.params.get("dl_mcs_max", 18)
            p[{14:0,18:1,22:2}.get(v,1)] = 1.0
        elif a.type in ("PRB_WEIGHT","SLICE_QOS"):
            w = a.params.get("weight",1.0)
            p[{0.8:0,1.0:1,1.2:2}.get(w,1)] = 1.0
        else:
            p[-1] = 1.0
        vecs.append(p)
        return np.concatenate(vecs, axis=0)


    def encode_playbook_onehot(self, pb: Playbook) -> np.ndarray: # Encodes actions into a one-hot vector (not smart encoding)
        K = PLAYBOOK_K; D = self.action_onehot_dim
        mat = np.zeros((K, D), dtype=np.float32)
        for i, a in enumerate(pb.actions[:K]):
            mat[i] = self._encode_action_np(a)
        return mat

    # --- API ---
    def score_playbooks(self, state_window: np.ndarray, playbooks: List[Playbook]) -> List[Tuple[Playbook, float]]:
        self.model.eval()
        with torch.no_grad():
            B = len(playbooks)
            state_batch = np.repeat(state_window[np.newaxis, :, :], B, axis=0)
            play_batch = np.stack([self.encode_playbook_onehot(pb) for pb in playbooks], axis=0)
            s = torch.tensor(state_batch, dtype=torch.float32, device=DEVICE)
            p = torch.tensor(play_batch, dtype=torch.float32, device=DEVICE)
            q = self.model(s, p).cpu().numpy().tolist()
        return list(zip(playbooks, q))

    def epsilon(self):
        t = min(self.steps, EPS_DECAY_STEPS)
        return EPS_END + (EPS_START - EPS_END) * math.exp(-5.0 * t / EPS_DECAY_STEPS)
    
    # --- training ---

    def learn_step(self):
        if len(self.replay) < BATCH_SIZE:
            return None
        s, p, r, s2, d = self.replay.sample(BATCH_SIZE)
        s = torch.tensor(s, dtype=torch.float32, device=DEVICE)
        p = torch.tensor(p, dtype=torch.float32, device=DEVICE)
        r = torch.tensor(r, dtype=torch.float32, device=DEVICE)
        s2 = torch.tensor(s2, dtype=torch.float32, device=DEVICE)
        d = torch.tensor(d, dtype=torch.float32, device=DEVICE)

        q = self.model(s, p)

        with torch.no_grad():
            # crude SARSA(0): use same playbook encoding as "next action"
            q2 = self.target(s2, p)
            y = r + GAMMA * (1.0 - d) * q2


        loss = F.mse_loss(q, y)
        self.optim.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
        self.optim.step()

        # soft target update
        with torch.no_grad():
            for tp, p_ in zip(self.target.parameters(), self.model.parameters()):
                tp.data.mul_(1 - TAU).add_(p_.data * TAU)

        return float(loss.item())
    
    def observe(self, s: np.ndarray, playbook: Playbook, r: float, s2: np.ndarray, done: bool):
        p = self.encode_playbook_onehot(playbook)   # [K,D]
        self.replay.push(s.astype(np.float32), p.astype(np.float32),
                         float(r), s2.astype(np.float32), bool(done))
        
    def load_offline(self, path="models/qnet_offline.pt"):
        ckpt = torch.load(path, map_location="cpu")
        meta = ckpt.get("meta", {})
        cell_cap = meta.get("cell_cap", len(self.cell_index))
        slice_cap = meta.get("slice_cap", len(self.slice_index))
        new = SlateDQNetwork(feat_dim=self.model.state_enc.gru.input_size, cell_cap=cell_cap, slice_cap=slice_cap).to(DEVICE)
        new.load_state_dict(ckpt["state_dict"])
        self.model = new
        self.target = SlateDQNetwork(feat_dim=self.model.state_enc.gru.input_size, cell_cap=cell_cap, slice_cap=slice_cap).to(DEVICE)
        self.target.load_state_dict(self.model.state_dict())
        self.action_onehot_dim = self.model.input_dim_action_onehot
        self.model.eval()






def main():    pass

if __name__ == "__main__":
    main()
