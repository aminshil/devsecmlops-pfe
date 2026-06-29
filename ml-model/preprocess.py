"""
Per-machine baseline + z-score normalisation — the heart of the design.

Two servers can have different healthy baselines (a web server idles at 30% CPU,
a DB server runs hot at 70%). The same raw number means opposite things on each.
So we convert every reading to a z-score against THAT machine's own mean/std:

    z = (value - machine_mean) / machine_std

After this, 0 always means "this machine's normal", and one model judges the
whole fleet with a single rule: z far from 0 = anomaly.

Reused by the trainer AND (later) the FastAPI serving layer:
  build_baselines(df_train, feature_cols)   -> baselines dict  (FIT ON TRAIN ONLY)
  apply_zscore(df, baselines, feature_cols) -> z-scored feature DataFrame
"""
import json
from pathlib import Path

import pandas as pd

STD_FLOOR = 1e-8  # a perfectly flat metric has std=0 -> would divide by zero


def build_baselines(df, feature_cols, machine_col="machine"):
    """Per-machine mean+std for each feature. FIT ON THE TRAIN SPLIT ONLY."""
    baselines = {}
    for machine, group in df.groupby(machine_col):
        baselines[str(machine)] = {}
        for col in feature_cols:
            mean = float(group[col].mean())
            std = float(group[col].std(ddof=0))   # population std
            if std < STD_FLOOR:                    # flat feature -> no blow-up
                std = 1.0
            baselines[str(machine)][col] = [mean, std]
    # global fallback for machines unseen at predict time
    baselines["__global__"] = {
        col: [float(df[col].mean()), float(max(df[col].std(ddof=0), 1.0))]
        for col in feature_cols
    }
    return baselines


def apply_zscore(df, baselines, feature_cols, machine_col="machine"):
    """Return a z-scored copy of feature_cols, using each row's machine baseline."""
    out = df[feature_cols].copy().astype(float)
    glob = baselines["__global__"]
    for machine, idx in df.groupby(machine_col).groups.items():
        stats = baselines.get(str(machine), glob)
        for col in feature_cols:
            mean, std = stats.get(col, glob[col])
            out.loc[idx, col] = (df.loc[idx, col] - mean) / std
    return out


def save_baselines(baselines, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(baselines, f, indent=2)


def load_baselines(path):
    with open(path) as f:
        return json.load(f)
