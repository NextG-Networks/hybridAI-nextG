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
        """Calculate reward across multiple SLO metrics."""
        total_improvement = 0.0
        weight_sum = 0.0
        
        for metric, config in slo_targets.items():
            if metric in prev_metrics and metric in curr_metrics:
                target = config['target']
                direction = config['direction']
                weight = config.get('weight', 1.0)
                
                prev_val = prev_metrics[metric]
                curr_val = curr_metrics[metric]
                
                if direction == "lower_better":
                    improvement = (prev_val - curr_val) / max(abs(target), 1e-6)
                else:
                    improvement = (curr_val - prev_val) / max(abs(target), 1e-6)
                
                total_improvement += improvement * weight
                weight_sum += weight
        
        if weight_sum > 0:
            return total_improvement / weight_sum
        else:
            return 0.0