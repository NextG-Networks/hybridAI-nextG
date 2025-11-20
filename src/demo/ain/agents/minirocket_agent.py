"""
Minirocket Deviation Detection Agent

Uses MiniRocket ML model to detect deviations in KPI stream.
Matches the system design: KPI Stream → Minirocket → Reasoner
"""

import asyncio
from typing import Dict, Any, Optional
from .utils import make_msg
from datetime import datetime, timezone, timedelta
from collections import deque

try:
    from ain.features.minirocket_rt import MiniRocketRT
    MINIROCKET_AVAILABLE = True
except ImportError:
    MINIROCKET_AVAILABLE = False
    print("[MinirocketAgent] MiniRocket not available, using fallback threshold detection")


class MinirocketAgent:
    """Detects deviations using MiniRocket ML model."""
    
    def __init__(self, bus, model_path: str = "models/minirocket.joblib", 
                 window_size: int = 128, metric: str = "delay_p95_ms",
                 debounce_seconds: float = 5.0, min_deviation_count: int = 3):
        """
        Args:
            bus: MemBus instance
            model_path: Path to trained MiniRocket model
            window_size: Window size for MiniRocket (default: 128)
            metric: Metric to monitor for deviations
            debounce_seconds: Minimum seconds between deviation reports (default: 5.0)
            min_deviation_count: Minimum consecutive deviations before reporting (default: 3)
        """
        self.bus = bus
        self.model_path = model_path
        self.window_size = window_size
        self.metric = metric
        self.debounce_seconds = debounce_seconds
        self.min_deviation_count = min_deviation_count
        
        # Debouncing state
        self.last_deviation_time: Optional[datetime] = None
        self.deviation_buffer: deque = deque(maxlen=min_deviation_count)  # Track recent deviations
        self.last_reported_value: Optional[float] = None
        
        # Initialize MiniRocket if available
        self.minirocket: Optional[MiniRocketRT] = None
        if MINIROCKET_AVAILABLE:
            try:
                self.minirocket = MiniRocketRT(model_path=model_path, win=window_size)
                print(f"[MinirocketAgent] Loaded model from {model_path}")
            except Exception as e:
                print(f"[MinirocketAgent] Failed to load model: {e}, using fallback")
                self.minirocket = None
        else:
            print("[MinirocketAgent] Using fallback threshold-based detection")
    
    async def run(self):
        """Subscribe to KPI stream and detect deviations."""
        q = await self.bus.sub("kpi.raw")
        
        while True:
            msg = await q.get()
            kpi = msg.payload.get("kpi", {})
            if not kpi:
                continue
            
            # Extract metric value
            cell_metrics = kpi.get("CellMetrics", {})
            value = cell_metrics.get(self.metric)
            
            if value is None:
                continue
            
            value = float(value)
            
            # Detect deviation
            is_deviation = False
            
            if self.minirocket:
                # Use MiniRocket ML model
                result = self.minirocket.push(value)
                if result and result.get("pred") == 1:  # 1 = deviation detected
                    is_deviation = True
            else:
                # Fallback: simple threshold-based detection
                # This is a placeholder - in real system, you'd have SLO targets
                # For now, we'll use a simple heuristic
                baseline = 40.0  # Default baseline
                if value > baseline * 1.2:  # 20% above baseline
                    is_deviation = True
            
            # Debouncing logic: only report if:
            # 1. We have enough consecutive deviations (min_deviation_count)
            # 2. Enough time has passed since last report (debounce_seconds)
            # 3. Value has changed significantly from last reported value
            now = datetime.now(timezone.utc)
            
            if is_deviation:
                self.deviation_buffer.append(True)
            else:
                self.deviation_buffer.append(False)
            
            # Check if we should report
            should_report = False
            if is_deviation and len(self.deviation_buffer) >= self.min_deviation_count:
                # Check if we have enough consecutive deviations
                recent_deviations = list(self.deviation_buffer)[-self.min_deviation_count:]
                if all(recent_deviations):
                    # Check cooldown period
                    if self.last_deviation_time is None:
                        should_report = True
                    else:
                        time_since_last = (now - self.last_deviation_time).total_seconds()
                        if time_since_last >= self.debounce_seconds:
                            # Check if value has changed significantly (at least 1% or 0.1ms for delay)
                            if self.last_reported_value is None:
                                should_report = True
                            else:
                                change_pct = abs(value - self.last_reported_value) / max(abs(self.last_reported_value), 0.01)
                                change_abs = abs(value - self.last_reported_value)
                                # For delay metrics, use absolute change (0.1ms threshold)
                                if "delay" in self.metric or "latency" in self.metric:
                                    should_report = change_abs >= 0.1
                                else:
                                    should_report = change_pct >= 0.01  # 1% change
            
            if should_report:
                deviation = self._create_deviation_event(kpi, value, confidence=0.9)
                # Publish deviation event
                await self.bus.pub("deviation.detected", make_msg(
                    "deviation.detected", "DEVIATION", "deviation.v1", deviation
                ))
                print(f"[MinirocketAgent] Deviation detected: {self.metric}={value:.2f} (debounced)")
                self.last_deviation_time = now
                self.last_reported_value = value
    
    def _create_deviation_event(self, kpi: Dict[str, Any], value: float, 
                                confidence: float = 0.8) -> Dict[str, Any]:
        """Create a deviation event from KPI data."""
        cell_metrics = kpi.get("CellMetrics", {})
        
        # Determine direction
        direction = "lower_better" if ("delay" in self.metric or "latency" in self.metric) else "higher_better"
        
        # Calculate severity (simplified)
        if direction == "lower_better":
            # For latency: higher is worse
            if value > 100:
                severity = "critical"
            elif value > 70:
                severity = "high"
            elif value > 50:
                severity = "medium"
            else:
                severity = "low"
        else:
            # For throughput: lower is worse
            if value < 10e6:
                severity = "critical"
            elif value < 20e6:
                severity = "high"
            elif value < 30e6:
                severity = "medium"
            else:
                severity = "low"
        
        return {
            "source": "minirocket",
            "metric": self.metric,
            "value": value,
            "baseline": None,  # Could be calculated from history
            "target": None,  # Will be set by reasoner based on SLO
            "direction": direction,
            "severity": severity,
            "confidence": confidence,
            "scope": {
                "cell_id": cell_metrics.get("cell_id") or "CELL_001",
                "region": "A",
                "service": "demo",
                "tenancy": "prod",
            },
            "evidence_ref": f"telemetry://minirocket/{self.metric}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

