#!/usr/bin/env python3
"""
Mock SLA monitoring test - demonstrates how to use the SLA config
in a monitoring system.
"""

import yaml
import random
import time
import os
from datetime import datetime
from typing import Dict, Any, List

class SLAMonitor:
    """Mock SLA monitoring system that uses the YAML configuration."""
    
    def __init__(self, config_file: str):
        with open(config_file, 'r') as file:
            self.config = yaml.safe_load(file)
        self.slos = self.config['sla']['slos']
        
    def simulate_metric_collection(self) -> Dict[str, float]:
        """Simulate collecting metrics from your testbed."""
        # Mock metric values (replace with real metric collection)
        return {
            'latency_ms': random.uniform(15, 25),  # Should be <= 20ms for 95%
            'jitter_ms': random.uniform(2, 8),     # Should be <= 5ms for 95%
            'throughput_mbps': random.uniform(8000, 12000),  # Should be >= 10Gbps
            'packet_loss_percent': random.uniform(0, 0.1),   # Should be <= 0.05%
            'uptime_percent': random.uniform(99.8, 100),     # Should be >= 99.9%
            'config_success_percent': random.uniform(98, 100), # Should be >= 99%
            'anomaly_detection_accuracy_percent': random.uniform(92, 98), # Should be >= 95%
            'self_heal_success_percent': random.uniform(96, 100), # Should be >= 98%
            'ai_decision_time_ms': random.uniform(50, 150),  # Should be <= 100ms
        }
    
    def check_slo_compliance(self, metrics: Dict[str, float]) -> List[Dict[str, Any]]:
        """Check if current metrics meet SLO requirements."""
        violations = []
        
        for slo in self.slos:
            metric_name = slo['metric']
            requirement = slo['requirement']
            
            if metric_name not in metrics:
                continue
                
            current_value = metrics[metric_name]
            
            # Parse requirement (simplified for demo)
            if '>=' in requirement:
                threshold = float(requirement.split('>=')[1].strip().replace('%', '').replace('Gbps', '000').replace('ms', ''))
                if current_value < threshold:
                    violations.append({
                        'slo_name': slo['name'],
                        'metric': metric_name,
                        'requirement': requirement,
                        'actual_value': current_value,
                        'severity': slo.get('alert', {}).get('severity', 'unknown')
                    })
            elif '<=' in requirement:
                threshold = float(requirement.split('<=')[1].strip().replace('%', '').replace('ms', ''))
                if current_value > threshold:
                    violations.append({
                        'slo_name': slo['name'],
                        'metric': metric_name,
                        'requirement': requirement,
                        'actual_value': current_value,
                        'severity': slo.get('alert', {}).get('severity', 'unknown')
                    })
            elif '95%' in requirement and '<=' in requirement:
                # Handle percentile requirements (simplified)
                threshold = float(requirement.split('<=')[1].strip().replace('ms', ''))
                if current_value > threshold:
                    violations.append({
                        'slo_name': slo['name'],
                        'metric': metric_name,
                        'requirement': requirement,
                        'actual_value': current_value,
                        'severity': slo.get('alert', {}).get('severity', 'unknown')
                    })
        
        return violations
    
    def simulate_monitoring_cycle(self, cycles: int = 5):
        """Simulate multiple monitoring cycles."""
        print("🔍 Starting SLA Monitoring Simulation")
        print("=" * 50)
        
        for cycle in range(1, cycles + 1):
            print(f"\n--- Monitoring Cycle {cycle} ---")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"Timestamp: {timestamp}")
            
            # Collect metrics
            metrics = self.simulate_metric_collection()
            
            # Check compliance
            violations = self.check_slo_compliance(metrics)
            
            # Report results
            if violations:
                print(f"🚨 {len(violations)} SLO violation(s) detected:")
                for violation in violations:
                    print(f"  - {violation['slo_name']}: {violation['actual_value']:.2f} "
                          f"(requirement: {violation['requirement']}) "
                          f"[{violation['severity']}]")
            else:
                print("✅ All SLOs are compliant")
            
            # Show some key metrics
            print("\nKey Metrics:")
            for metric, value in metrics.items():
                print(f"  {metric}: {value:.2f}")
            
            if cycle < cycles:
                time.sleep(1)  # Brief pause between cycles
        
        print("\n" + "=" * 50)
        print("🏁 Monitoring simulation complete")

def main():
    """Demo the SLA monitoring functionality."""
    try:
        # Get the directory of this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Navigate to project root and find config file
        project_root = os.path.dirname(os.path.dirname(script_dir))
        config_file = os.path.join(project_root, "config", "slaSlo.yaml")
        
        monitor = SLAMonitor(config_file)
        print(f"📊 Loaded SLA config for: {monitor.config['sla']['name']}")
        print(f"📈 Monitoring {len(monitor.slos)} SLOs")
        
        monitor.simulate_monitoring_cycle(cycles=3)
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()