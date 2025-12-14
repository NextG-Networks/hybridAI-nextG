#!/usr/bin/env python3
"""
Plot loss function for stable and unstable AI models.

Usage:
    python plot_loss.py --stable stable_loss.json --unstable unstable_loss.json
    python plot_loss.py --stable stable_loss.json  # Plot only stable
    python plot_loss.py --from-logs stable.log unstable.log  # Extract from log files
"""

import argparse
import json
import re
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional


def load_loss_from_json(json_path: str) -> Dict:
    """Load loss history from JSON file."""
    with open(json_path, 'r') as f:
        return json.load(f)


def extract_loss_from_logs(log_path: str) -> List[Dict]:
    """Extract loss values from log file."""
    loss_data = []
    pattern = r'\[LEARNING\].*Step=(\d+).*loss=([\d.]+)'
    
    with open(log_path, 'r') as f:
        for line in f:
            match = re.search(pattern, line)
            if match:
                step = int(match.group(1))
                loss = float(match.group(2))
                loss_data.append({'step': step, 'loss': loss})
    
    return loss_data


def plot_loss(
    stable_data: Optional[List[Dict]] = None,
    unstable_data: Optional[List[Dict]] = None,
    output_path: str = "loss_plot.png",
    title: str = "AI Model Loss Function"
):
    """Plot loss curves for stable and unstable models."""
    fig, ax = plt.subplots(figsize=(12, 6))
    
    if stable_data:
        steps = [d['step'] for d in stable_data]
        losses = [d['loss'] for d in stable_data]
        ax.plot(steps, losses, label='Stable Model (with target network)', 
                color='blue', linewidth=2, alpha=0.7)
    
    if unstable_data:
        steps = [d['step'] for d in unstable_data]
        losses = [d['loss'] for d in unstable_data]
        ax.plot(steps, losses, label='Unstable Model (no target network)', 
                color='red', linewidth=2, alpha=0.7)
    
    ax.set_xlabel('Training Step', fontsize=12)
    ax.set_ylabel('Loss (MSE)', fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    
    # Add statistics
    if stable_data:
        stable_losses = [d['loss'] for d in stable_data]
        stable_final = stable_losses[-1] if stable_losses else 0
        stable_mean = np.mean(stable_losses) if stable_losses else 0
        ax.text(0.02, 0.98, 
                f'Stable: Final={stable_final:.4f}, Mean={stable_mean:.4f}',
                transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', 
                facecolor='lightblue', alpha=0.5))
    
    if unstable_data:
        unstable_losses = [d['loss'] for d in unstable_data]
        unstable_final = unstable_losses[-1] if unstable_losses else 0
        unstable_mean = np.mean(unstable_losses) if unstable_losses else 0
        ax.text(0.02, 0.90 if stable_data else 0.98,
                f'Unstable: Final={unstable_final:.4f}, Mean={unstable_mean:.4f}',
                transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round',
                facecolor='lightcoral', alpha=0.5))
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"Loss plot saved to: {output_path}")
    plt.close()


def main():
    parser = argparse.ArgumentParser(description='Plot loss function for AI models')
    parser.add_argument('--stable', type=str, help='Path to stable model loss JSON file')
    parser.add_argument('--unstable', type=str, help='Path to unstable model loss JSON file')
    parser.add_argument('--from-logs', nargs='+', help='Extract loss from log files (stable first, then unstable)')
    parser.add_argument('--output', type=str, default='loss_plot.png', help='Output plot file path')
    parser.add_argument('--title', type=str, default='AI Model Loss Function', help='Plot title')
    
    args = parser.parse_args()
    
    stable_data = None
    unstable_data = None
    
    if args.from_logs:
        # Extract from log files
        if len(args.from_logs) >= 1:
            stable_data = extract_loss_from_logs(args.from_logs[0])
            print(f"Extracted {len(stable_data)} loss entries from stable log: {args.from_logs[0]}")
        if len(args.from_logs) >= 2:
            unstable_data = extract_loss_from_logs(args.from_logs[1])
            print(f"Extracted {len(unstable_data)} loss entries from unstable log: {args.from_logs[1]}")
    else:
        # Load from JSON files
        if args.stable:
            stable_data = load_loss_from_json(args.stable)
            print(f"Loaded {len(stable_data)} loss entries from stable model: {args.stable}")
        
        if args.unstable:
            unstable_data = load_loss_from_json(args.unstable)
            print(f"Loaded {len(unstable_data)} loss entries from unstable model: {args.unstable}")
    
    if not stable_data and not unstable_data:
        print("Error: No loss data provided. Use --stable, --unstable, or --from-logs")
        return
    
    plot_loss(stable_data, unstable_data, args.output, args.title)


if __name__ == '__main__':
    main()

