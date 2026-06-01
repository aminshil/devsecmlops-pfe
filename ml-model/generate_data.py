"""
Synthetic IT-infrastructure KPI generator.
POC data for the anomaly-detection use case (CPU, RAM, network).
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# --- Settings ---
N_TOTAL = 1000
ANOMALY_RATIO = 0.05
SEED = 42

rng = np.random.default_rng(SEED)

n_anomaly = int(N_TOTAL * ANOMALY_RATIO)
n_normal = N_TOTAL - n_anomaly

# --- Normal data (healthy server) ---
cpu_normal = rng.normal(40, 10, n_normal).clip(0, 100)
ram_normal = rng.normal(60, 10, n_normal).clip(0, 100)
net_normal = rng.normal(100, 20, n_normal).clip(0, None)

# --- Anomalies: 70% obvious, 30% subtle ---
n_obvious = int(n_anomaly * 0.7)
n_subtle = n_anomaly - n_obvious

# Obvious: all three KPIs elevated
cpu_obv = rng.normal(85, 6, n_obvious).clip(0, 100)
ram_obv = rng.normal(88, 5, n_obvious).clip(0, 100)
net_obv = rng.normal(600, 120, n_obvious).clip(0, None)

# Subtle: only one KPI spikes
cpu_sub = rng.normal(40, 10, n_subtle).clip(0, 100)
ram_sub = rng.normal(60, 10, n_subtle).clip(0, 100)
net_sub = rng.normal(100, 20, n_subtle).clip(0, None)
which = rng.integers(0, 3, n_subtle)
cpu_sub[which == 0] = rng.normal(90, 4, (which == 0).sum()).clip(0, 100)
ram_sub[which == 1] = rng.normal(92, 3, (which == 1).sum()).clip(0, 100)
net_sub[which == 2] = rng.normal(550, 100, (which == 2).sum()).clip(0, None)

# --- Combine + shuffle ---
cpu = np.concatenate([cpu_normal, cpu_obv, cpu_sub])
ram = np.concatenate([ram_normal, ram_obv, ram_sub])
net = np.concatenate([net_normal, net_obv, net_sub])
labels = np.concatenate([
    np.zeros(n_normal, dtype=int),
    np.ones(n_obvious + n_subtle, dtype=int),
])

idx = rng.permutation(N_TOTAL)
cpu, ram, net, labels = cpu[idx], ram[idx], net[idx], labels[idx]

# --- Timestamps (1 per minute) ---
start = datetime(2026, 1, 1, 0, 0, 0)
timestamps = [start + timedelta(minutes=i) for i in range(N_TOTAL)]

# --- Save ---
df = pd.DataFrame({
    "timestamp": timestamps,
    "cpu": cpu.round(3),
    "ram": ram.round(3),
    "network": net.round(3),
    "label": labels,
})

output_path = Path(__file__).resolve().parent.parent / "data" / "data.csv"
output_path.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(output_path, index=False)

print(f"Wrote {len(df)} rows to {output_path}")
print(f"  Normal:  {(df['label'] == 0).sum()}")
print(f"  Anomaly: {(df['label'] == 1).sum()}")
