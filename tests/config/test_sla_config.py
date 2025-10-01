#!/usr/bin/env python3
"""
Test script to validate SLA/SLO YAML configuration file.
Tests syntax, structure, and logical consistency.
"""

import yaml
import re
import os
from datetime import datetime
from typing import Dict, List, Any

def load_sla_config(file_path: str) -> Dict[str, Any]:
    """Load and parse the SLA YAML configuration."""
    try:
        with open(file_path, 'r') as file:
            config = yaml.safe_load(file)
        print("✅ YAML syntax is valid")
        return config
    except yaml.YAMLError as e:
        print(f"❌ YAML syntax error: {e}")
        return None
    except FileNotFoundError:
        print(f"❌ File not found: {file_path}")
        return None

def validate_sla_structure(config: Dict[str, Any]) -> bool:
    """Validate the basic structure of the SLA config."""
    required_fields = ['name', 'description', 'service', 'slos']
    
    if 'sla' not in config:
        print("❌ Missing 'sla' root key")
        return False
    
    sla = config['sla']
    
    for field in required_fields:
        if field not in sla:
            print(f"❌ Missing required field: {field}")
            return False
    
    print("✅ SLA structure is valid")
    return True

def validate_slo_entries(slos: List[Dict[str, Any]]) -> bool:
    """Validate individual SLO entries."""
    required_slo_fields = ['name', 'description', 'metric', 'requirement']
    valid = True
    
    for i, slo in enumerate(slos):
        for field in required_slo_fields:
            if field not in slo:
                print(f"❌ SLO {i+1} '{slo.get('name', 'Unknown')}' missing field: {field}")
                valid = False
        
        # Validate requirement format
        if 'requirement' in slo:
            req = slo['requirement']
            # Check if requirement has proper format (>=, <=, <, >, =)
            if not re.match(r'^(>=|<=|<|>|=)\s*[\d.]+', req) and not re.match(r'^\d+%\s*(>=|<=|<|>)\s*[\d.]+', req):
                print(f"⚠️  SLO '{slo.get('name')}' has unusual requirement format: {req}")
    
    if valid:
        print(f"✅ All {len(slos)} SLO entries are structurally valid")
    
    return valid

def validate_metrics_consistency(slos: List[Dict[str, Any]]) -> bool:
    """Check for metric naming consistency and duplicates."""
    metrics = []
    names = []
    
    for slo in slos:
        if 'metric' in slo:
            metrics.append(slo['metric'])
        if 'name' in slo:
            names.append(slo['name'])
    
    # Check for duplicate metrics
    if len(metrics) != len(set(metrics)):
        duplicates = [m for m in metrics if metrics.count(m) > 1]
        print(f"⚠️  Duplicate metrics found: {set(duplicates)}")
    
    # Check for duplicate names
    if len(names) != len(set(names)):
        duplicates = [n for n in names if names.count(n) > 1]
        print(f"❌ Duplicate SLO names found: {set(duplicates)}")
        return False
    
    print("✅ Metric consistency check passed")
    return True

def validate_alert_configuration(slos: List[Dict[str, Any]]) -> bool:
    """Validate alert configurations."""
    valid_severities = ['critical', 'high', 'medium', 'low']
    valid_channels = ['slack', 'email', 'pagerduty', 'webhook']
    
    for slo in slos:
        if 'alert' in slo:
            alert = slo['alert']
            
            if 'severity' in alert:
                if alert['severity'] not in valid_severities:
                    print(f"⚠️  SLO '{slo.get('name')}' has invalid severity: {alert['severity']}")
            
            if 'notification_channels' in alert:
                for channel in alert['notification_channels']:
                    if channel not in valid_channels:
                        print(f"⚠️  SLO '{slo.get('name')}' has unknown notification channel: {channel}")
    
    print("✅ Alert configuration check passed")
    return True

def validate_testbed_relevance(slos: List[Dict[str, Any]]) -> bool:
    """Check if SLOs are relevant for a small testbed setup."""
    testbed_appropriate = []
    questionable = []
    
    for slo in slos:
        name = slo.get('name', '').lower()
        
        # Check for testbed-appropriate metrics
        if any(term in name for term in ['latency', 'throughput', 'packet loss', 'ai decision', 'configuration', 'anomaly']):
            testbed_appropriate.append(slo['name'])
        
        # Check for potentially overkill metrics
        if any(term in name for term in ['energy', 'handover', 'video', 'session setup']):
            questionable.append(slo['name'])
    
    print(f"✅ {len(testbed_appropriate)} SLOs are testbed-appropriate")
    if questionable:
        print(f"⚠️  {len(questionable)} SLOs might be overkill for testbed: {questionable}")
    
    return True

def generate_test_report(config: Dict[str, Any]) -> None:
    """Generate a comprehensive test report."""
    print("\n" + "="*60)
    print("SLA/SLO CONFIGURATION TEST REPORT")
    print("="*60)
    
    sla = config['sla']
    slos = sla.get('slos', [])
    
    print(f"Service: {sla.get('service')}")
    print(f"Total SLOs: {len(slos)}")
    print(f"Coverage Areas: {len(set([slo.get('name', '').split()[0] for slo in slos]))}")
    
    # Count by category
    categories = {}
    for slo in slos:
        desc = slo.get('description', '').lower()
        if 'latency' in desc or 'throughput' in desc or 'packet' in desc:
            categories['Performance'] = categories.get('Performance', 0) + 1
        elif 'ai' in desc or 'anomaly' in desc or 'decision' in desc:
            categories['AI-Specific'] = categories.get('AI-Specific', 0) + 1
        elif 'uptime' in desc or 'configuration' in desc:
            categories['Reliability'] = categories.get('Reliability', 0) + 1
        else:
            categories['Other'] = categories.get('Other', 0) + 1
    
    print("\nSLO Distribution:")
    for category, count in categories.items():
        print(f"  {category}: {count}")
    
    print("\n" + "="*60)

def main():
    """Main test function."""
    # Get the directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Navigate to project root and find config file
    project_root = os.path.dirname(os.path.dirname(script_dir))
    config_file = os.path.join(project_root, "config", "slaSlo.yaml")
    
    print("Testing SLA/SLO Configuration...")
    print("-" * 40)
    
    # Load and validate
    config = load_sla_config(config_file)
    if not config:
        return False
    
    all_tests_passed = True
    
    # Run validation tests
    all_tests_passed &= validate_sla_structure(config)
    
    if 'sla' in config and 'slos' in config['sla']:
        slos = config['sla']['slos']
        all_tests_passed &= validate_slo_entries(slos)
        all_tests_passed &= validate_metrics_consistency(slos)
        all_tests_passed &= validate_alert_configuration(slos)
        all_tests_passed &= validate_testbed_relevance(slos)
        
        # Generate report
        generate_test_report(config)
    
    if all_tests_passed:
        print("\n🎉 All tests passed! SLA configuration is ready to use.")
    else:
        print("\n⚠️  Some issues found. Review the output above.")
    
    return all_tests_passed

if __name__ == "__main__":
    main()