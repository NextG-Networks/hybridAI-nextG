"""
Adaptive Reasoner Agent with Static SLOs and Baseline Learning

- SLOs: Static, loaded from operator-defined JSON file
- Baseline: Learned from environment for context understanding
- Reasoner: Uses SLO as target, baseline to understand optimization potential
"""

import asyncio
import logging
import json
from pathlib import Path
from typing import Dict, Any, Optional
import numpy as np
from collections import deque
from datetime import datetime, timezone
from .utils import make_msg
from ain.loop.observer_rl import Intent
from ain.common.log_config import should_log, LOG_INTENT

logger = logging.getLogger(__name__)


class SLOConfig:
    """Static SLO configuration loaded from JSON file."""
    
    def __init__(self, slo_file: Optional[str] = None):
        """
        Args:
            slo_file: Path to SLO JSON file (e.g., "configs/slos.json")
        """
        self.slos: Dict[str, Dict[str, Any]] = {}  # metric -> SLO config
        self.slo_file = slo_file or "configs/slos.json"
        self._load_slos()
    
    def _load_slos(self):
        """Load SLOs from JSON file."""
        slo_path = Path(self.slo_file)
        if not slo_path.exists():
            logger.warning(f"[SLO] SLO file not found: {self.slo_file}, using defaults")
            return
        
        try:
            with open(slo_path, 'r') as f:
                data = json.load(f)
            
            # Expected format:
            # {
            #   "slos": [
            #     {
            #       "metric": "DRB_PdcpSduDelayDl",
            #       "target": 40.0,
            #       "direction": "lower_better",
            #       "tolerance": 0.1,  # 10% tolerance
            #       "priority": "high"
            #     },
            #     ...
            #   ]
            # }
            
            slos_list = data.get("slos", [])
            for slo in slos_list:
                metric = slo.get("metric")
                if metric:
                    self.slos[metric] = {
                        "target": float(slo.get("target")),
                        "direction": slo.get("direction", "lower_better"),
                        "tolerance": float(slo.get("tolerance", 0.1)),  # 10% default
                        "priority": slo.get("priority", "medium"),
                        "slo_id": slo.get("slo_id", f"slo_{metric}"),
                    }
            
            if should_log(LOG_INTENT):
                logger.info(f"[SLO] Loaded {len(self.slos)} SLOs from {self.slo_file}")
                for metric, config in self.slos.items():
                    logger.info(f"[SLO]   {metric}: target={config['target']}, direction={config['direction']}")
        
        except Exception as e:
            logger.error(f"[SLO] Error loading SLO file: {e}", exc_info=True)
    
    def get_slo(self, metric: str) -> Optional[Dict[str, Any]]:
        """Get SLO configuration for a metric."""
        return self.slos.get(metric)
    
    def is_metric_tracked(self, metric: str) -> bool:
        """Check if metric has an SLO defined."""
        return metric in self.slos
    
    def meets_slo(self, metric: str, value: float) -> bool:
        """Check if current value meets SLO target."""
        slo = self.get_slo(metric)
        if not slo:
            return True  # No SLO defined, assume it's met
        
        target = slo["target"]
        tolerance = slo["tolerance"]
        direction = slo["direction"]
        
        if direction == "lower_better":
            # Value should be <= target (with tolerance)
            return value <= target * (1 + tolerance)
        elif direction == "higher_better":
            # Value should be >= target (with tolerance)
            return value >= target * (1 - tolerance)
        else:
            # Moderate: value should be within range
            return abs(value - target) / max(abs(target), 0.001) <= tolerance


class BaselineLearner:
    """Learns baseline distributions from observed KPIs for context."""
    
    def __init__(self, min_samples: int = 50, window_size: int = 200):
        self.min_samples = min_samples
        self.window_size = window_size
        self.metric_baselines: Dict[str, deque] = {}
        self.metric_stats: Dict[str, Dict[str, float]] = {}
        self.is_learned: Dict[str, bool] = {}
    
    def add_observation(self, metric: str, value: float):
        """Add a KPI observation to learn baseline."""
        if metric not in self.metric_baselines:
            self.metric_baselines[metric] = deque(maxlen=self.window_size)
            self.is_learned[metric] = False
        
        self.metric_baselines[metric].append(value)
        
        if len(self.metric_baselines[metric]) >= self.min_samples:
            self._update_stats(metric)
            if not self.is_learned[metric]:
                self.is_learned[metric] = True
                if should_log(LOG_INTENT):
                    stats = self.metric_stats[metric]
                    logger.info(f"[BASELINE] Learned baseline for {metric}: "
                              f"mean={stats['mean']:.2f}, p50={stats['p50']:.2f}, p90={stats['p90']:.2f}")
    
    def _update_stats(self, metric: str):
        """Update statistical measures for a metric."""
        values = np.array(list(self.metric_baselines[metric]))
        
        self.metric_stats[metric] = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "median": float(np.median(values)),
            "p10": float(np.percentile(values, 10)),
            "p25": float(np.percentile(values, 25)),
            "p50": float(np.percentile(values, 50)),
            "p75": float(np.percentile(values, 75)),
            "p90": float(np.percentile(values, 90)),
            "p95": float(np.percentile(values, 95)),
        }
    
    def get_baseline(self, metric: str) -> Optional[Dict[str, float]]:
        """Get baseline statistics for a metric."""
        return self.metric_stats.get(metric)
    
    def is_environment_optimal(self, metric: str, value: float, direction: str) -> bool:
        """
        Check if value is already optimal in this environment (based on baseline).
        Used to understand if optimization is even possible.
        """
        baseline = self.get_baseline(metric)
        if not baseline:
            return False  # Don't know yet
        
        if direction == "lower_better":
            # If we're already at p10 or better, environment is optimal
            return value <= baseline.get("p10", baseline["p50"])
        elif direction == "higher_better":
            # If we're already at p90 or better, environment is optimal
            return value >= baseline.get("p90", baseline["p50"])
        return False


class EnhancedReasonerAgent:
    """
    Reasoner that uses static SLOs and learned baseline for context-aware intent creation.
    
    Logic:
    1. SLO defines the target (static, operator-defined)
    2. Baseline learns what's normal in this environment
    3. Create intent if: value doesn't meet SLO AND optimization is possible
    """
    
    def __init__(self, bus, knowledge_base=None, use_llm: bool = False, 
                 slo_file: Optional[str] = None):
        """
        Args:
            bus: MemBus instance
            knowledge_base: Knowledge base instance (e.g., CacheLibrary)
            use_llm: Not used, kept for compatibility
            slo_file: Path to SLO JSON file (default: "configs/slos.json")
        """
        self.bus = bus
        self.knowledge_base = knowledge_base
        self.use_llm = use_llm  # Not used, kept for compatibility
        
        self.current_intent: Optional[Dict[str, Any]] = None
        self.active_intent_id: Optional[str] = None
        
        # Static SLO configuration
        self.slo_config = SLOConfig(slo_file=slo_file)
        
        # Baseline learning for context
        self.baseline_learner = BaselineLearner(min_samples=50, window_size=200)
    
    async def run(self):
        """Subscribe to deviation events and KPIs."""
        q_deviation = await self.bus.sub("deviation.detected")
        q_kpi = await self.bus.sub("kpi.raw")  # Track KPIs for baseline learning
        
        # Learn baseline from KPIs in background
        asyncio.create_task(self._learn_baseline_from_kpis(q_kpi))
        
        while True:
            msg = await q_deviation.get()
            deviation = msg.payload
            metric = deviation.get("metric")
            value = deviation.get("value")
            
            if should_log(LOG_INTENT):
                logger.info(f"[INTENT] Received deviation: {metric}={value:.2f}, severity={deviation.get('severity')}")
            
            # Check if metric has SLO defined
            slo = self.slo_config.get_slo(metric)
            if not slo:
                if should_log(LOG_INTENT):
                    logger.debug(f"[INTENT] No SLO defined for {metric} - skipping")
                continue
            
            # Add to baseline learner for context
            self.baseline_learner.add_observation(metric, value)
            
            # Check if current value meets SLO
            if self.slo_config.meets_slo(metric, value):
                if should_log(LOG_INTENT):
                    logger.info(f"[INTENT] Value meets SLO (metric={metric}, value={value:.2f} <= target={slo['target']:.2f}) - skipping")
                continue
            
            # Value doesn't meet SLO - check if optimization is possible
            baseline = self.baseline_learner.get_baseline(metric)
            if baseline:
                # Check if environment is already optimal (can't optimize further)
                if self.baseline_learner.is_environment_optimal(metric, value, slo["direction"]):
                    if should_log(LOG_INTENT):
                        logger.warning(f"[INTENT] SLO violation but environment already optimal "
                                     f"(metric={metric}, value={value:.2f}, SLO={slo['target']:.2f}) - "
                                     f"creating intent anyway (SLO may be too aggressive)")
                else:
                    if should_log(LOG_INTENT):
                        logger.info(f"[INTENT] SLO violation detected (metric={metric}, value={value:.2f} > target={slo['target']:.2f})")
            
            # Check if we already have an active intent for this metric
            if self.current_intent and self.current_intent.get("metric") == metric:
                current_target = self.current_intent.get("target")
                # Only update if SLO target is different
                if abs(slo["target"] - current_target) / max(abs(current_target), 0.001) < 0.01:
                    continue  # SLO target hasn't changed
            
            # Create intent to meet SLO
            await self._create_intent_from_slo(metric, value, slo)
    
    async def _learn_baseline_from_kpis(self, q):
        """Continuously learn baseline from KPI stream."""
        while True:
            msg = await q.get()
            kpi = msg.payload.get("kpi", {})
            cell_metrics = kpi.get("CellMetrics", {})
            
            # Track metrics that have SLOs defined
            for metric in self.slo_config.slos.keys():
                value = cell_metrics.get(metric)
                if value is not None:
                    self.baseline_learner.add_observation(metric, float(value))
    
    async def _create_intent_from_slo(self, metric: str, value: float, slo: Dict[str, Any]):
        """Create and publish intent to meet SLO target."""
        try:
            # Determine intent type
            if slo["direction"] == "lower_better":
                intent_type = "REDUCE_LATENCY"
            elif slo["direction"] == "higher_better":
                intent_type = "INCREASE_THROUGHPUT"
            else:
                intent_type = "OPTIMIZE_UTILIZATION"
            
            # Use SLO target (static, operator-defined)
            target = slo["target"]
            
            # Create RL Intent
            rl_intent = Intent(
                type=intent_type,
                metric=metric,
                target=float(target),
                direction=slo["direction"],
                action_cost=0.01,
                reward_clip=2.0,
            )
            
            # Store current intent
            self.current_intent = {
                "intent_id": f"intent_{metric}_{datetime.now(timezone.utc).timestamp()}",
                "metric": rl_intent.metric,
                "target": rl_intent.target,
                "direction": rl_intent.direction,
                "type": rl_intent.type,
                "slo_id": slo.get("slo_id"),
            }
            self.active_intent_id = self.current_intent["intent_id"]
            
            # Publish intent to membus
            await self.bus.pub("intent.current", make_msg(
                "intent.current", "INTENT", "intent.v1", self.current_intent
            ))
            
            # Also publish RL intent format for observer
            await self.bus.pub("intent.rl", make_msg(
                "intent.rl", "RL_INTENT", "rl_intent.v1", {
                    "type": rl_intent.type,
                    "metric": rl_intent.metric,
                    "target": rl_intent.target,
                    "direction": rl_intent.direction,
                    "action_cost": rl_intent.action_cost,
                    "reward_clip": rl_intent.reward_clip,
                }
            ))
            
            baseline = self.baseline_learner.get_baseline(metric)
            if should_log(LOG_INTENT):
                baseline_info = f"baseline_p50={baseline['p50']:.2f}" if baseline else "baseline_learning"
                logger.info(f"[INTENT] Created intent: {rl_intent.type} for {rl_intent.metric} "
                          f"(SLO_target={target:.2f}, current={value:.2f}, {baseline_info})")
            
        except Exception as e:
            logger.error(f"[INTENT] Error creating intent from SLO: {e}", exc_info=True)
