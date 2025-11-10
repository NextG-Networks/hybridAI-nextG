# From chatGPT just a basic priniciple
import numpy as np

class Observer:
    def __init__(self, predictor, intent):
        self.predictor = predictor
        self.intent = intent
        self.prev_kpi = None

    def preprocess_kpi(self, kpi_snapshot: dict) -> np.ndarray:
        """Convert KPI snapshot to numeric vector."""
        # Select and normalize a few key metrics
        return np.array([
            kpi_snapshot["thr_dl_bps"] / 1e6,
            kpi_snapshot["thr_ul_bps"] / 1e6,
            kpi_snapshot["bler_dl"],
            kpi_snapshot["bler_ul"],
            kpi_snapshot["delay_p95_ms"] / 100.0,
        ], dtype=np.float32)

    def compute_reward(self, prev_kpi, curr_kpi, playbook) -> float:
        """Compute reward based on current intent."""
        if self.intent["type"] == "REDUCE_LATENCY":
            prev = prev_kpi["delay_p95_ms"]
            curr = curr_kpi["delay_p95_ms"]
            delta = (prev - curr) / max(prev, 1e-6)
            penalty = 1.0 if curr > self.intent["target"] else 0.0
            action_cost = 0.01 * len(playbook.actions)
            return float(np.clip(delta - penalty - action_cost, -2.0, 2.0))
        return 0.0

    def on_new_kpi(self, new_kpi: dict, last_playbook):
        """Called when a new KPI snapshot arrives."""
        state_next = self.preprocess_kpi(new_kpi)
        if self.prev_kpi is not None:
            # Compute reward
            r = self.compute_reward(self.prev_kpi, new_kpi, last_playbook)
            s = self.preprocess_kpi(self.prev_kpi)
            s2 = state_next
            p = self.predictor.encode_playbook_onehot(last_playbook)
            self.predictor.replay.push(s, p, r, s2, False)
            self.predictor.learn_step(feat_dim=len(s))
        self.prev_kpi = new_kpi
