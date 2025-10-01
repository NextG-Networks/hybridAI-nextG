import numpy as np, pandas as pd


def make_series(n=6000, seed=0):
    """Synthetic KPI: baseline + sinusoid + noise + injected spikes."""
    rng = np.random.default_rng(seed)
    base = 10 + np.sin(np.linspace(0, 80, n)) + 0.5 * rng.standard_normal(n)
    # inject spikes (deviations)
    spikes = rng.choice(np.arange(200, n - 200), size=12, replace=False)
    y = np.zeros(n, dtype=int)
    for s in spikes:
        base[s : s + 30] += 8 + 2 * rng.standard_normal(30)
        y[s : s + 30] = 1
    return pd.Series(base, name="latency_ms"), pd.Series(y, name="label")
