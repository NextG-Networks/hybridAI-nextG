# observer_rl.py - Enhanced with Contextual Bandit capabilities
from __future__ import annotations
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple, Any

import numpy as np
import torch

# Import contextual bandit components
try:
    from ain.bandit.context_extractor import ContextExtractor, NetworkContext
    from ain.bandit.reward_calculator import SLORewardCalculator
    BANDIT_AVAILABLE = True
except ImportError:
    # Fallback if bandit components not yet created
    BANDIT_AVAILABLE = False
    print("[Warning] Contextual bandit components not found. Using basic mode.")


@dataclass
class Intent:
    type: str                 # e.g., "REDUCE_LATENCY", "INCREASE_THROUGHPUT"
    metric: str               # e.g., "delay_p95_ms"
    target: float             # e.g., 40.0
    direction: str = "lower_better"  # "lower_better" | "higher_better"
    action_cost: float = 0.01        # penalty per action in playbook
    reward_clip: float = 2.0         # clip absolute reward


class RLObserver:
    def __init__(
        self,
        predictor,
        intent: Intent,
        kpi_file: str = "fake_kpi_stream.json",
        window: int = 12,
        features: Optional[List[str]] = None,
        use_internal_encoder: bool = False,
        device: Optional[torch.device] = None,
        enable_contextual_bandit: bool = True,  # NEW: Feature flag
    ):
        self.predictor = predictor
        self.intent = intent
        self.kpi_file = Path(kpi_file)
        self.window = window
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.buf: Deque[np.ndarray] = deque(maxlen=window)
        self.last_kpi_raw: Optional[Dict] = None
        self.last_state_win: Optional[np.ndarray] = None  # [W, F]
        self.use_internal_encoder = use_internal_encoder

        # NEW: Contextual bandit components
        self.enable_contextual_bandit = enable_contextual_bandit and BANDIT_AVAILABLE
        if self.enable_contextual_bandit:
            self.context_extractor = ContextExtractor(window_size=window)
            self.slo_reward_calculator = SLORewardCalculator(
                action_cost=intent.action_cost,
                reward_clip=intent.reward_clip
            )
            self.current_context: Optional[NetworkContext] = None
            self.context_history: List[NetworkContext] = []
            print("[Observer] Contextual bandit mode enabled")
        else:
            print("[Observer] Basic mode (no contextual bandit)")

        # Select features (defaults chosen from your schema)
        self.features = features or [
            "thr_dl_bps",
            "thr_ul_bps",
            "bler_dl",
            "bler_ul",
            "cqi_avg",
            "mcs_dl_avg",
            "mcs_ul_avg",
            "active_ue_count",
            "prb_used_dl_ratio",
            "delay_p95_ms",
        ]

        # Simple fixed scalers (adjust as you like)
        self.scalers = {
            "thr_dl_bps": 1e6,  # scale to Mbps
            "thr_ul_bps": 1e6,
            "cqi_avg": 15.0,
            "mcs_dl_avg": 28.0,
            "mcs_ul_avg": 28.0,
            "active_ue_count": 100.0,
            "delay_p95_ms": 100.0,
        }

    # ---------- KPI reading & preprocessing ----------
    def _read_latest_kpi(self) -> Optional[Dict]:
        if not self.kpi_file.exists():
            return None
        with open(self.kpi_file, "r") as f:
            data = json.load(f)
        stream = data.get("kpi_stream", [])
        if not stream:
            return None
        return stream[-1]

    def _extract_features_row(self, kpi: Dict) -> np.ndarray:
        cell = kpi.get("CellMetrics", {})
        # derived ratios
        prb_total = max(float(cell.get("prb_total", 1.0)), 1.0)
        prb_used_dl = float(cell.get("prb_used_dl", 0.0))
        # build feature vector in configured order
        vals: List[float] = []
        for name in self.features:
            if name == "prb_used_dl_ratio":
                v = prb_used_dl / prb_total
            else:
                v = float(cell.get(name, 0.0))
            # scale
            scale = self.scalers.get(name, 1.0)
            v = v / scale
            vals.append(v)
        return np.asarray(vals, dtype=np.float32)

    def _update_window(self, row: np.ndarray) -> np.ndarray:
        from copy import deepcopy
        self.buf.append(row)
        if len(self.buf) < self.window:
            # Left-pad with the first row until window is full (simple pad)
            first = deepcopy(self.buf[0])
            padded = [first] * (self.window - len(self.buf)) + list(self.buf)
            win = np.stack(padded, axis=0)
        else:
            win = np.stack(list(self.buf), axis=0)
        self.last_state_win = win
        return win  # [W, F]

    # ---------- NEW: Context extraction methods ----------
    def _extract_context(self, kpi: Dict) -> Optional[NetworkContext]:
        """Extract rich network context from KPI data."""
        if not self.enable_contextual_bandit:
            return None
        
        try:
            context = self.context_extractor.extract_from_kpi(kpi)
            return context
        except Exception as e:
            print(f"[Observer] Context extraction error: {e}")
            return None

    def _update_context_history(self, context: NetworkContext):
        """Maintain context history for temporal analysis."""
        if not self.enable_contextual_bandit:
            return
        
        self.context_history.append(context)
        if len(self.context_history) > self.window:
            self.context_history.pop(0)

    def _classify_network_situation(self, context: NetworkContext) -> str:
        """Classify current network situation for contextual decisions."""
        if not context:
            return "unknown"
        
        # Multi-criteria situation classification
        situations = []
        
        if context.latency_ms > 70:
            situations.append("high_latency")
        if context.throughput_dl_mbps < 50:
            situations.append("low_throughput")
        if context.bler > 0.05:
            situations.append("high_error_rate")
        if context.network_load > 0.8:
            situations.append("high_load")
        if context.quality_index < 0.5:
            situations.append("poor_quality")
        
        if not situations:
            return "normal"
        elif len(situations) == 1:
            return situations[0]
        else:
            # Multiple issues - return most critical
            if "high_latency" in situations:
                return "high_latency"
            elif "high_error_rate" in situations:
                return "high_error_rate"
            else:
                return situations[0]

    def _calculate_context_bonus(self, playbook: Any, context: NetworkContext) -> float:
        """Calculate bonus reward for contextually appropriate actions."""
        if not playbook or not context or not self.enable_contextual_bandit:
            return 0.0
        
        bonus = 0.0
        situation = self._classify_network_situation(context)
        actions = getattr(playbook, 'actions', [])
        
        for action in actions:
            action_bonus = 0.0
            
            # Get action details
            action_type = getattr(action, 'type', '')
            action_params = getattr(action, 'params', {})
            
            # Situation-specific bonuses
            if situation == "high_latency":
                if action_type == "SCHEDULER_POLICY":
                    policy = action_params.get('policy', '')
                    if policy == 'MAX_THROUGHPUT':
                        action_bonus = 0.15  # Good for latency
                    elif policy == 'PF':
                        action_bonus = 0.05  # Somewhat good
                elif action_type == "MCS_CAP":
                    mcs_cap = action_params.get('dl_mcs_max', 15)
                    if mcs_cap >= 20:
                        action_bonus = 0.10  # Higher MCS might help latency
                elif action_type == "PRB_WEIGHT":
                    weight = action_params.get('weight', 1.0)
                    if weight > 1.0:
                        action_bonus = 0.08  # More PRBs might help latency
            
            elif situation == "low_throughput":
                if action_type == "PRB_WEIGHT":
                    weight = action_params.get('weight', 1.0)
                    if weight > 1.0:
                        action_bonus = 0.20  # Excellent for throughput
                elif action_type == "SCHEDULER_POLICY":
                    policy = action_params.get('policy', '')
                    if policy == 'PF':
                        action_bonus = 0.15  # Good for throughput
                elif action_type == "MCS_CAP":
                    mcs_cap = action_params.get('dl_mcs_max', 15)
                    if mcs_cap >= 18:
                        action_bonus = 0.10  # Higher MCS for throughput
            
            elif situation == "high_error_rate":
                if action_type == "MCS_CAP":
                    mcs_cap = action_params.get('dl_mcs_max', 15)
                    if mcs_cap <= 16:
                        action_bonus = 0.18  # Lower MCS for reliability
                elif action_type == "PRB_WEIGHT":
                    weight = action_params.get('weight', 1.0)
                    if weight > 1.0:
                        action_bonus = 0.10  # More resources for reliability
            
            elif situation == "poor_quality":
                if action_type == "MCS_CAP":
                    mcs_cap = action_params.get('dl_mcs_max', 15)
                    if mcs_cap <= 18:
                        action_bonus = 0.12  # Conservative MCS
                elif action_type == "SCHEDULER_POLICY":
                    policy = action_params.get('policy', '')
                    if policy == 'PF':
                        action_bonus = 0.08  # Fair scheduling for quality
            
            bonus += action_bonus
        
        # Cap total bonus
        return min(bonus, 0.5)  # Maximum 0.5 bonus per playbook

    def _extract_metrics_dict(self, kpi: Dict) -> Dict[str, float]:
        """Extract metrics as dictionary for reward calculation."""
        cell = kpi.get("CellMetrics", {})
        return {
            "delay_p95_ms": float(cell.get("delay_p95_ms", 0.0)),
            "thr_dl_bps": float(cell.get("thr_dl_bps", 0.0)),
            "thr_ul_bps": float(cell.get("thr_ul_bps", 0.0)),
            "bler_dl": float(cell.get("bler_dl", 0.0)),
            "bler_ul": float(cell.get("bler_ul", 0.0)),
            "cqi_avg": float(cell.get("cqi_avg", 0.0)),
            "mcs_dl_avg": float(cell.get("mcs_dl_avg", 0.0)),
            "active_ue_count": float(cell.get("active_ue_count", 0.0)),
            "prb_used_dl": float(cell.get("prb_used_dl", 0.0)),
        }

    # ---------- Enhanced Reward Calculation ----------
    def _compute_reward(self, prev_kpi: Dict, curr_kpi: Dict, actions_len: int) -> float:
        """Original reward computation (fallback)."""
        metric = self.intent.metric
        target = self.intent.target
        direction = self.intent.direction

        prev = float(prev_kpi.get("CellMetrics", {}).get(metric, 0.0))
        curr = float(curr_kpi.get("CellMetrics", {}).get(metric, 0.0))

        if direction == "lower_better":
            delta_raw = (prev - curr)
            delta_rel = delta_raw / max(abs(prev), 1e-6)
            violation = 1.0 if curr > target else 0.0
        else:  # higher_better
            delta_raw = (curr - prev)
            delta_rel = delta_raw / max(abs(prev), 1e-6)
            violation = 1.0 if curr < target else 0.0

        r = delta_rel
        r -= self.intent.action_cost * float(actions_len)
        r -= violation  # penalty if target not met
        clip = self.intent.reward_clip
        return float(np.clip(r, -clip, clip))

    def _compute_enhanced_reward(self, prev_kpi: Dict, curr_kpi: Dict, 
                               last_playbook: Any) -> float:
        """Enhanced reward computation with contextual bonus."""
        # Get base reward
        actions_len = len(getattr(last_playbook, "actions", []))
        base_reward = self._compute_reward(prev_kpi, curr_kpi, actions_len)
        
        if not self.enable_contextual_bandit or not self.current_context:
            return base_reward
        
        # Calculate contextual bonus
        context_bonus = self._calculate_context_bonus(last_playbook, self.current_context)
        
        # Alternative: Use SLO reward calculator
        try:
            prev_metrics = self._extract_metrics_dict(prev_kpi)
            curr_metrics = self._extract_metrics_dict(curr_kpi)
            
            enhanced_reward = self.slo_reward_calculator.calculate_contextual_reward(
                prev_metrics=prev_metrics,
                curr_metrics=curr_metrics,
                intent=self.intent,
                num_actions=actions_len,
                context_bonus=context_bonus
            )
            
            return enhanced_reward
            
        except Exception as e:
            print(f"[Observer] Enhanced reward calculation failed: {e}")
            return base_reward + context_bonus

    # ---------- Public API ----------
    def step(self, last_playbook) -> Optional[np.ndarray]:
        """Enhanced step with contextual intelligence."""
        kpi = self._read_latest_kpi()
        if kpi is None:
            return None

        # NEW: Extract context
        if self.enable_contextual_bandit:
            context = self._extract_context(kpi)
            if context:
                self.current_context = context
                self._update_context_history(context)

        row = self._extract_features_row(kpi)
        s2 = self._update_window(row)  # [W, F]

        # If we have a previous KPI, compute reward and push to replay
        if self.last_kpi_raw is not None and self.last_state_win is not None:
            s = self.last_state_win  # previous window [W, F]
            
            # Enhanced reward calculation
            if self.enable_contextual_bandit:
                r = self._compute_enhanced_reward(self.last_kpi_raw, kpi, last_playbook)
            else:
                r = self._compute_reward(self.last_kpi_raw, kpi, len(getattr(last_playbook, "actions", [])))
            
            p = self.predictor.encode_playbook_onehot(last_playbook)  # [K, D]

            # Push transition to replay and learn
            self.predictor.replay.push(s, p, r, s2, False)
            self.predictor.learn_step()

        # Update previous pointers
        self.last_kpi_raw = kpi

        return s2  # current window

    # ---------- NEW: Public context access methods ----------
    def get_current_context(self) -> Optional[NetworkContext]:
        """Get current network context."""
        return self.current_context

    def get_context_situation(self) -> str:
        """Get current network situation classification."""
        if self.current_context and self.enable_contextual_bandit:
            return self._classify_network_situation(self.current_context)
        return "unknown"

    def get_context_features(self) -> Optional[np.ndarray]:
        """Get normalized context features for external use."""
        if self.current_context and self.enable_contextual_bandit:
            return self.context_extractor.to_feature_vector(self.current_context)
        return None

    def get_context_tensor(self) -> Optional[np.ndarray]:
        """Get context as [W, F] tensor."""
        if self.current_context and self.enable_contextual_bandit:
            return self.context_extractor.to_state_tensor(self.current_context)
        return None

    def get_context_history_summary(self) -> Dict[str, Any]:
        """Get summary of recent context history."""
        if not self.enable_contextual_bandit or not self.context_history:
            return {}
        
        recent_contexts = self.context_history[-5:]  # Last 5 contexts
        
        return {
            "avg_latency": np.mean([c.latency_ms for c in recent_contexts]),
            "avg_throughput": np.mean([c.throughput_dl_mbps for c in recent_contexts]),
            "avg_bler": np.mean([c.bler for c in recent_contexts]),
            "avg_network_load": np.mean([c.network_load for c in recent_contexts]),
            "avg_quality_index": np.mean([c.quality_index for c in recent_contexts]),
            "situation_trend": [self._classify_network_situation(c) for c in recent_contexts],
            "history_length": len(self.context_history)
        }


if __name__ == "__main__":
    # Simple test
    from dataclasses import dataclass
    
    @dataclass
    class MockPredictor:
        def encode_playbook_onehot(self, playbook):
            return np.array([[1, 0, 0]])
        
        class MockReplay:
            def push(self, s, p, r, s2, done):
                print(f"Replay: reward={r:.3f}")
        
        replay = MockReplay()
        
        def learn_step(self):
            pass
    
    intent = Intent("REDUCE_LATENCY", "delay_p95_ms", 40.0)
    observer = RLObserver(MockPredictor(), intent, enable_contextual_bandit=False)
    print(f"Observer initialized with contextual bandit: {observer.enable_contextual_bandit}")