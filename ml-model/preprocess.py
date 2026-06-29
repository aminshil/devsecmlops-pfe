"""
Per-machine baseline + z-score normalisation.

Fallback chain (best to worst):
  1. Per-machine  — machine seen at training time (most accurate)
  2. Per-type     — machine type known (e.g. all 'web' machines averaged)
  3. Global       — completely unknown machine/type

Why z-score per machine?
  A web server idles at 30% CPU; a DB server runs hot at 75%.
  The same raw number means opposite things on each machine.
  After z-scoring, 0 always means "this machine's normal" and the
  Isolation Forest judges the whole fleet with a single decision boundary.

Used by trainers AND the FastAPI serving layer.
"""
import json
from pathlib import Path

import pandas as pd

STD_FLOOR = 1e-8   # flat metric (std=0) → avoid division by zero


def build_baselines(df, feature_cols, machine_col="machine", type_col="type"):
    """
    Build per-machine mean+std baselines. FIT ON TRAIN SPLIT ONLY.

    Also builds per-type averages as a fallback for machines not seen
    at training time but whose type is known.

    Returns a dict:
      baselines[machine_id][col] = [mean, std]
      baselines["__type__web"][col] = [mean, std]   # type fallback
      baselines["__global__"][col] = [mean, std]    # global fallback
      baselines["__feature_order__"] = [col, ...]   # column order
    """
    baselines = {}

    # 1. Per-machine baselines
    for machine, group in df.groupby(machine_col):
        baselines[str(machine)] = {}
        for col in feature_cols:
            mean = float(group[col].mean())
            std  = float(group[col].std(ddof=0))
            baselines[str(machine)][col] = [mean, max(std, STD_FLOOR)]

        # Store machine type if available
        if type_col in df.columns:
            baselines[str(machine)]["__type__"] = str(group[type_col].iloc[0])

    # 2. Per-type baselines (average of all machines of that type)
    if type_col in df.columns:
        for mtype, group in df.groupby(type_col):
            key = f"__type__{mtype}"
            baselines[key] = {}
            for col in feature_cols:
                mean = float(group[col].mean())
                std  = float(group[col].std(ddof=0))
                baselines[key][col] = [mean, max(std, STD_FLOOR)]

    # 3. Global fallback
    baselines["__global__"] = {
        col: [float(df[col].mean()), float(max(df[col].std(ddof=0), 1.0))]
        for col in feature_cols
    }

    # 4. Feature order (so API builds vectors in the right column order)
    baselines["__feature_order__"] = list(feature_cols)

    return baselines


def get_stats(baselines, machine, machine_type=None):
    """
    Return the best available baseline stats for a machine.
    Fallback chain: per-machine → per-type → global.
    """
    if machine in baselines:
        return baselines[machine]
    if machine_type:
        type_key = f"__type__{machine_type}"
        if type_key in baselines:
            return baselines[type_key]
    return baselines["__global__"]


def apply_zscore(df, baselines, feature_cols,
                 machine_col="machine", type_col="type"):
    """
    Return a z-scored copy of feature_cols.
    Each row uses its machine's own baseline (fallback chain above).
    """
    out  = df[feature_cols].copy().astype(float)
    glob = baselines["__global__"]

    for machine, idx in df.groupby(machine_col).groups.items():
        mtype = None
        if type_col in df.columns:
            mtype = str(df.loc[idx, type_col].iloc[0])
        stats = get_stats(baselines, str(machine), mtype)

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
