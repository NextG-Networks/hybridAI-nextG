#!/usr/bin/env python3
"""
Train MiniRocket model for deviation detection using xApp KPI data.

This script:
1. Reads KPI data from CSV files (gnb_kpis.csv, ue_kpis.csv)
2. Extracts time series for key metrics (delay_p95_ms, etc.)
3. Labels deviations based on thresholds or anomaly detection
4. Trains MiniRocket + classifier
5. Saves model for use in MinirocketAgent
"""

from __future__ import annotations
import argparse
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from sktime.transformations.panel.rocket import MiniRocket
from sklearn.linear_model import RidgeClassifierCV
from sklearn.model_selection import train_test_split
import sys

# Add parent directory to path
THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR.parent.parent.parent))


def load_xapp_kpis(gnb_csv: str, ue_csv: str, metric: str = "delay_p95_ms") -> pd.Series:
    """
    Load KPI data from CSV files and extract time series for a specific metric.
    
    Args:
        gnb_csv: Path to gnb_kpis.csv
        ue_csv: Path to ue_kpis.csv  
        metric: Metric to extract (e.g., "delay_p95_ms", "UE_PDCP_Delay_DL_ms")
    
    Returns:
        Series with metric values over time
    """
    # Try to load from gnb_kpis.csv first
    if Path(gnb_csv).exists():
        df_gnb = pd.read_csv(gnb_csv)
        if metric in df_gnb.columns:
            # Use non-null values
            values = df_gnb[metric].dropna()
            if len(values) > 0:
                print(f"Loaded {len(values)} values for {metric} from {gnb_csv}")
                return values
    
    # Try ue_kpis.csv
    if Path(ue_csv).exists():
        df_ue = pd.read_csv(ue_csv)
        if metric in df_ue.columns:
            values = df_ue[metric].dropna()
            if len(values) > 0:
                print(f"Loaded {len(values)} values for {metric} from {ue_csv}")
                return values
    
    raise ValueError(f"Metric {metric} not found in CSV files")


def label_deviations(series: pd.Series, method: str = "threshold", 
                     threshold: float = None, percentile: float = 90.0) -> pd.Series:
    """
    Label deviations in time series.
    
    Args:
        series: Time series values
        method: "threshold" (absolute), "percentile" (relative), or "statistical" (z-score)
        threshold: Absolute threshold for "threshold" method
        percentile: Percentile threshold for "percentile" method
    
    Returns:
        Series with labels (0=normal, 1=deviation)
    """
    labels = pd.Series(0, index=series.index, dtype=int)
    
    if method == "threshold":
        if threshold is None:
            # Default: use 75th percentile as threshold
            threshold = series.quantile(0.75)
        labels[series > threshold] = 1
        print(f"Threshold method: threshold={threshold:.2f}, deviations={labels.sum()}/{len(labels)}")
    
    elif method == "percentile":
        threshold = series.quantile(percentile / 100.0)
        labels[series > threshold] = 1
        print(f"Percentile method: {percentile}th percentile={threshold:.2f}, deviations={labels.sum()}/{len(labels)}")
    
    elif method == "statistical":
        # Z-score based: deviation if > mean + 2*std
        mean = series.mean()
        std = series.std()
        threshold = mean + 2 * std
        labels[series > threshold] = 1
        print(f"Statistical method: mean={mean:.2f}, std={std:.2f}, threshold={threshold:.2f}, deviations={labels.sum()}/{len(labels)}")
    
    return labels


def to_windows(x: pd.Series, y: pd.Series, win: int = 128, step: int = 32):
    """
    Convert time series to sliding windows.
    
    Args:
        x: Time series values
        y: Labels (0=normal, 1=deviation)
        win: Window size
        step: Step size for sliding window
    
    Returns:
        X: DataFrame with windows (sktime format)
        Y: Array with window labels (1 if any point in window is deviation)
    """
    X, Y = [], []
    for s in range(0, len(x) - win + 1, step):
        e = s + win
        window_x = x.iloc[s:e]
        window_y = y.iloc[s:e]
        X.append(pd.Series(window_x.values))
        # Window label = 1 if any point in window is a deviation
        Y.append(int(window_y.max()))
    return pd.DataFrame({"signal": X}), np.array(Y)


def main():
    parser = argparse.ArgumentParser(description="Train MiniRocket model for xApp KPI deviation detection")
    parser.add_argument("--gnb-csv", type=str, default="gnb_kpis.csv", help="Path to gnb_kpis.csv")
    parser.add_argument("--ue-csv", type=str, default="ue_kpis.csv", help="Path to ue_kpis.csv")
    parser.add_argument("--metric", type=str, default="delay_p95_ms", 
                       help="Metric to train on (e.g., delay_p95_ms, UE_PDCP_Delay_DL_ms)")
    parser.add_argument("--window-size", type=int, default=128, help="Window size for MiniRocket")
    parser.add_argument("--step-size", type=int, default=32, help="Step size for sliding windows")
    parser.add_argument("--method", type=str, default="percentile", 
                       choices=["threshold", "percentile", "statistical"],
                       help="Method for labeling deviations")
    parser.add_argument("--threshold", type=float, default=None, 
                       help="Threshold value (for threshold method)")
    parser.add_argument("--percentile", type=float, default=85.0,
                       help="Percentile threshold (for percentile method)")
    parser.add_argument("--output", type=str, default="models/minirocket_xapp.joblib",
                       help="Output path for trained model")
    parser.add_argument("--test-size", type=float, default=0.3, help="Test set size")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Training MiniRocket Model for xApp KPI Deviation Detection")
    print("=" * 60)
    
    # Load KPI data
    print(f"\n1. Loading KPI data for metric: {args.metric}")
    try:
        series = load_xapp_kpis(args.gnb_csv, args.ue_csv, args.metric)
        print(f"   Loaded {len(series)} data points")
        print(f"   Range: [{series.min():.2f}, {series.max():.2f}]")
        print(f"   Mean: {series.mean():.2f}, Std: {series.std():.2f}")
    except Exception as e:
        print(f"   ERROR: {e}")
        print(f"   Available columns in gnb_kpis.csv:")
        if Path(args.gnb_csv).exists():
            df = pd.read_csv(args.gnb_csv)
            print(f"     {list(df.columns)}")
        return
    
    # Label deviations
    print(f"\n2. Labeling deviations (method: {args.method})")
    labels = label_deviations(series, method=args.method, 
                             threshold=args.threshold, 
                             percentile=args.percentile)
    
    if labels.sum() == 0:
        print("   WARNING: No deviations found! Model may not train well.")
        print("   Try adjusting --percentile or --threshold")
    
    # Create windows
    print(f"\n3. Creating sliding windows (window={args.window_size}, step={args.step_size})")
    X, Y = to_windows(series, labels, win=args.window_size, step=args.step_size)
    print(f"   Created {len(X)} windows")
    print(f"   Normal windows: {(Y == 0).sum()}, Deviation windows: {(Y == 1).sum()}")
    
    if (Y == 1).sum() == 0:
        print("   ERROR: No deviation windows found! Cannot train model.")
        return
    
    # Train-test split
    print(f"\n4. Splitting data (test_size={args.test_size})")
    Xtr, Xte, ytr, yte = train_test_split(
        X, Y, test_size=args.test_size, stratify=Y, random_state=42
    )
    print(f"   Train: {len(Xtr)} windows ({ytr.sum()} deviations)")
    print(f"   Test: {len(Xte)} windows ({yte.sum()} deviations)")
    
    # Train MiniRocket
    print(f"\n5. Training MiniRocket transformer...")
    mr = MiniRocket()
    mr.fit(Xtr)
    Xtr_f = mr.transform(Xtr)
    Xte_f = mr.transform(Xte)
    print(f"   Transformed features: {Xtr_f.shape[1]} dimensions")
    
    # Train classifier
    print(f"\n6. Training Ridge Classifier...")
    clf = RidgeClassifierCV(alphas=np.logspace(-3, 3, 13))
    clf.fit(Xtr_f, ytr)
    
    # Evaluate
    train_acc = clf.score(Xtr_f, ytr)
    test_acc = clf.score(Xte_f, yte)
    print(f"   Train accuracy: {train_acc:.3f}")
    print(f"   Test accuracy: {test_acc:.3f}")
    
    # Save model
    print(f"\n7. Saving model to {args.output}")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "mr": mr,
        "clf": clf,
        "meta": {
            "metric": args.metric,
            "window_size": args.window_size,
            "method": args.method,
            "train_acc": float(train_acc),
            "test_acc": float(test_acc),
            "n_windows": len(X),
            "n_deviations": int(Y.sum()),
        }
    }, args.output)
    print(f"   ✓ Model saved successfully!")
    
    print("\n" + "=" * 60)
    print("Training complete!")
    print(f"Use this model with: --minirocket-model {args.output}")
    print("=" * 60)


if __name__ == "__main__":
    main()

