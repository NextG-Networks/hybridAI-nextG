"""
Deviation Detector Agent

Monitors KPI stream and detects when metrics deviate from targets.
Publishes deviation events that the reasoner can use to create intents.
"""

import asyncio
from typing import Dict, Any, Optional, List
from .utils import make_msg
from datetime import datetime, timezone


class DeviationDetectorAgent:
    """Detects deviations in KPI stream and publishes deviation events."""
    
    def __init__(self, bus, default_targets: Optional[Dict[str, float]] = None, 
                 deviation_threshold: float = 0.1):
        """
        Args:
            bus: MemBus instance
            default_targets: Dict of metric -> target value (e.g., {"delay_p95_ms": 40.0})
            deviation_threshold: Relative threshold for deviation (0.1 = 10% deviation)
        """
        self.bus = bus
        self.default_targets = default_targets or {
            "delay_p95_ms": 40.0,
            "thr_dl_bps": 50e6,
        }
        self.deviation_threshold = deviation_threshold
        self.current_intent: Optional[Dict[str, Any]] = None
        self.metric_history: Dict[str, List[float]] = {}  # Track metric history for baseline
        
    async def run(self):
        """Subscribe to KPIs and detect deviations."""
        q_kpi = await self.bus.sub("kpi.raw")
        q_intent = await self.bus.sub("intent.current")
        
        # Listen for intent updates
        asyncio.create_task(self._listen_intent(q_intent))
        
        while True:
            msg = await q_kpi.get()
            kpi = msg.payload.get("kpi", {})
            if not kpi:
                continue
            
            # Detect deviations
            deviations = self._detect_deviations(kpi)
            
            for dev in deviations:
                # Publish deviation event
                await self.bus.pub("deviation.detected", make_msg(
                    "deviation.detected", "DEVIATION", "deviation.v1", dev
                ))
                print(f"[DeviationDetector] Detected deviation: {dev['metric']}={dev['value']:.2f} (target={dev.get('target', 'N/A')})")
    
    async def _listen_intent(self, q):
        """Listen for intent updates to know current targets."""
        while True:
            msg = await q.get()
            self.current_intent = msg.payload
            # Extract targets from intent
            if self.current_intent:
                metric = self.current_intent.get("metric")
                target = self.current_intent.get("target")
                if metric and target:
                    self.default_targets[metric] = target
    
    def _detect_deviations(self, kpi: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Detect deviations in KPI data.
        
        Returns list of deviation events.
        """
        deviations = []
        cell_metrics = kpi.get("CellMetrics", {})
        
        # Check each metric against targets
        for metric, target in self.default_targets.items():
            value = cell_metrics.get(metric)
            if value is None:
                continue
            
            value = float(value)
            
            # Determine direction (lower_better for delay/latency, higher_better for throughput)
            direction = "lower_better" if ("delay" in metric or "latency" in metric) else "higher_better"
            
            # Check if metric deviates from target
            is_deviating = False
            if direction == "lower_better":
                # For lower_better: deviation if value > target * (1 + threshold)
                threshold_value = target * (1 + self.deviation_threshold)
                if value > threshold_value:
                    is_deviating = True
            else:
                # For higher_better: deviation if value < target * (1 - threshold)
                threshold_value = target * (1 - self.deviation_threshold)
                if value < threshold_value:
                    is_deviating = True
            
            if is_deviating:
                # Calculate severity based on how far from target
                if direction == "lower_better":
                    deviation_pct = ((value - target) / target) * 100
                else:
                    deviation_pct = ((target - value) / target) * 100
                
                if deviation_pct > 50:
                    severity = "critical"
                elif deviation_pct > 25:
                    severity = "high"
                elif deviation_pct > 10:
                    severity = "medium"
                else:
                    severity = "low"
                
                # Update metric history
                if metric not in self.metric_history:
                    self.metric_history[metric] = []
                self.metric_history[metric].append(value)
                if len(self.metric_history[metric]) > 10:
                    self.metric_history[metric].pop(0)
                
                # Calculate baseline (average of recent values)
                baseline = sum(self.metric_history[metric]) / len(self.metric_history[metric])
                
                deviation = {
                    "source": "kpi_monitor",
                    "metric": metric,
                    "value": value,
                    "baseline": baseline,
                    "target": target,
                    "direction": direction,
                    "severity": severity,
                    "deviation_pct": deviation_pct,
                    "scope": {
                        "cell_id": cell_metrics.get("cell_id") or "CELL_001",
                        "region": "A",
                        "service": "demo",
                        "tenancy": "prod",
                    },
                    "evidence_ref": f"telemetry://kpi/{metric}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                
                deviations.append(deviation)
        
        return deviations

