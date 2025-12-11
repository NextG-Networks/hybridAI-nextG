import numpy as np
from typing import Dict, Any, Optional

class SLORewardCalculator:
    """Enhanced SLO-based reward calculator for contextual bandit."""
    
    def __init__(self, action_cost: float = 0.01, reward_clip: float = 2.0):
        self.action_cost = action_cost
        self.reward_clip = reward_clip
        
    def calculate_contextual_reward(self, 
                                  prev_metrics: Dict[str, float],
                                  curr_metrics: Dict[str, float], 
                                  intent: Any,
                                  num_actions: int,
                                  context_bonus: float = 0.0) -> float:
        """Calculate enhanced reward with contextual bonus."""
        
        # Base reward calculation (compatible with your existing logic)
        metric_key = intent.metric
        target = intent.target
        direction = intent.direction
        
        prev_val = prev_metrics.get(metric_key, 0.0)
        curr_val = curr_metrics.get(metric_key, 0.0)
        
        # Calculate improvement
        if direction == "lower_better":
            delta_raw = (prev_val - curr_val)
            delta_rel = delta_raw / max(abs(prev_val), 1e-6)
            violation = 1.0 if curr_val > target else 0.0
        else:  # higher_better
            delta_raw = (curr_val - prev_val)
            delta_rel = delta_raw / max(abs(prev_val), 1e-6)
            violation = 1.0 if curr_val < target else 0.0
        
        # Enhanced reward components
        improvement_reward = delta_rel
        action_penalty = self.action_cost * float(num_actions)
        violation_penalty = violation
        
        # NEW: Contextual bonus for smart action selection
        contextual_bonus = context_bonus
        
        # Total reward
        total_reward = improvement_reward - action_penalty - violation_penalty + contextual_bonus
        
        return float(np.clip(total_reward, -self.reward_clip, self.reward_clip))
    
    def calculate_multi_metric_reward(self,
                                    prev_metrics: Dict[str, float],
                                    curr_metrics: Dict[str, float],
                                    slo_targets: Dict[str, Dict]) -> float:
        """Calculate reward across multiple SLO metrics, including violation penalties."""
        import numpy as np
        total_improvement = 0.0
        total_violation = 0.0
        weight_sum = 0.0
        
        for metric, config in slo_targets.items():
            prev_val = prev_metrics.get(metric)
            curr_val = curr_metrics.get(metric)
            
            # Skip if either value is missing or NaN
            if prev_val is None or curr_val is None:
                continue
            if np.isnan(prev_val) or np.isnan(curr_val):
                continue
            
            target = config['target']
            direction = config['direction']
            weight = config.get('weight', 1.0)
            
            # Calculate improvement using relative change (like single-metric reward)
            # Use previous value as denominator for consistency with single-metric calculation
            if direction == "lower_better":
                delta_raw = (prev_val - curr_val)
                improvement = delta_raw / max(abs(prev_val), 1e-6)
                # Violation: current value exceeds target (for lower_better)
                # Penalty proportional to how much we exceed the target
                if curr_val > target:
                    violation = (curr_val - target) / max(abs(target), 1e-6)  # Relative violation
                else:
                    violation = 0.0
            elif direction == "higher_better":
                delta_raw = (curr_val - prev_val)
                improvement = delta_raw / max(abs(prev_val), 1e-6)
                # Violation: current value below target (for higher_better)
                # Penalty proportional to how much we're below the target
                if curr_val < target:
                    violation = (target - curr_val) / max(abs(target), 1e-6)  # Relative violation
                else:
                    violation = 0.0
            elif direction == "moderate_better":
                # For moderate_better: reward being close to target, penalize being too far in either direction
                # Improvement: reward moving closer to target
                distance_prev = abs(prev_val - target)
                distance_curr = abs(curr_val - target)
                improvement = (distance_prev - distance_curr) / max(abs(target), 1e-6)  # Positive if getting closer
                # Violation: penalty for being far from target (either too high or too low)
                violation = distance_curr / max(abs(target), 1e-6)  # Relative distance from target
            else:
                # Unknown direction, default to higher_better
                delta_raw = (curr_val - prev_val)
                improvement = delta_raw / max(abs(prev_val), 1e-6)
                if curr_val < target:
                    violation = (target - curr_val) / max(abs(target), 1e-6)
                else:
                    violation = 0.0
            
            total_improvement += improvement * weight * 100.0
            total_violation += violation * weight
            weight_sum += weight
            
            # NEW: Add explicit improvement bonus for any positive change
            # This prevents learned helplessness from always-negative rewards
            if improvement > 0:
                improvement_bonus = 0.5 * improvement * weight  # 50% bonus for improvements
                total_improvement += improvement_bonus
        
        if weight_sum > 0:
            # Average improvement and violation across all metrics
            avg_improvement = total_improvement / weight_sum
            avg_violation = total_violation / weight_sum
            # Combine: improvement reward minus violation penalty
            # Violation penalty is weighted by how bad the violation is relative to target
            return avg_improvement - avg_violation
        else:
            # No valid metrics found - return 0 (will be penalized by action cost)
            return 0.0