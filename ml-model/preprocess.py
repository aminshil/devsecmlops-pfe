"""
Per-machine PER-TIME-WINDOW baseline + z-score normalisation.

Fallback chain (best to worst):
  1. Per-machine + window  — machine seen at training time, in this time window
  2. Per-machine (any time) — machine seen, but this window has no baseline
  3. Per-type              — machine type known (e.g. all 'web' machines averaged)
  4. Global               — completely unknown machine/type

Why per-time-window z-score?
  A web server idles at 30% CPU at night and runs at 60% during the day.
  A single all-day baseline (mean=45%) would rate a 50% night reading as normal
  even though it is anomalous for 3am. Splitting the baseline into
  night/morning/afternoon/evening lets 0 mean "normal for this machine AT THIS
  TIME OF DAY", so the Isolation Forest catches subtle time-dependent anomalies.

Time windows:
  night     = 00:00-05:59
  morning   = 06:00-11:59
  afternoon = 12:00-17:59
  evening   = 18:00-23:59

Used by trainers AND the FastAPI serving layer.
"""
import json
from pathlib import Path

import pandas as pd

STD_FLOOR = 1e-8   # flat metric (std=0) -> avoid division by zero

WINDOWS = ("night", "morning", "afternoon", "evening")


def hour_to_window(hour: int) -> str:
    """Map an hour (0-23) to its time window."""
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def add_window_column(df, timestamp_col="timestamp"):
    """Add an integer 'hour' and string 'window' column derived from timestamp."""
    ts = pd.to_datetime(df[timestamp_col])
    df = df.copy()
    df["hour"] = ts.dt.hour
    df["window"] = df["hour"].map(hour_to_window)
    return df


def build_baselines(df, feature_cols,
                    machine_col="machine", type_col="type",
                    window_col="window"):
    """
    Build per-machine per-window mean+std baselines. FIT ON TRAIN SPLIT ONLY.

    Requires df to already have a 'window' column (use add_window_column first).

    Returns a dict:
      baselines["<machine>|<window>"][col] = [mean, std]   # primary
      baselines["<machine>"][col]          = [mean, std]   # machine fallback
      baselines["__type__<type>"][col]     = [mean, std]   # type fallback
      baselines["__global__"][col]         = [mean, std]   # global fallback
      baselines["<machine>"]["__type__"]   = "<type>"      # machine's type
      baselines["__feature_order__"]       = [col, ...]
      baselines["__windows__"]             = ["night", ...]
      baselines["__has_windows__"]         = True
    """
    if window_col not in df.columns:
        raise ValueError(
            "build_baselines expects a 'window' column. "
            "Call add_window_column(df) first."
        )

    baselines = {}

    # 1. Per-machine per-window baselines (primary)
    for (machine, window), group in df.groupby([machine_col, window_col]):
        key = f"{machine}|{window}"
        baselines[key] = {}
        for col in feature_cols:
            mean = float(group[col].mean())
            std  = float(group[col].std(ddof=0))
            baselines[key][col] = [mean, max(std, STD_FLOOR)]

    # 2. Per-machine (any time) baselines — fallback if a window is missing
    for machine, group in df.groupby(machine_col):
        key = str(machine)
        baselines[key] = {}
        for col in feature_cols:
            mean = float(group[col].mean())
            std  = float(group[col].std(ddof=0))
            baselines[key][col] = [mean, max(std, STD_FLOOR)]
        if type_col in df.columns:
            baselines[key]["__type__"] = str(group[type_col].iloc[0])

    # 3. Per-type baselines
    if type_col in df.columns:
        for mtype, group in df.groupby(type_col):
            key = f"__type__{mtype}"
            baselines[key] = {}
            for col in feature_cols:
                mean = float(group[col].mean())
                std  = float(group[col].std(ddof=0))
                baselines[key][col] = [mean, max(std, STD_FLOOR)]

    # 4. Global fallback
    baselines["__global__"] = {
        col: [float(df[col].mean()), float(max(df[col].std(ddof=0), 1.0))]
        for col in feature_cols
    }

    # 5. Metadata
    baselines["__feature_order__"] = list(feature_cols)
    baselines["__windows__"]       = list(WINDOWS)
    baselines["__has_windows__"]   = True
    return baselines


def get_stats(baselines, machine, window=None, machine_type=None):
    """
    Return (stats, level) using the fallback chain:
      machine+window -> machine -> type -> global
    'level' is one of: "machine+window" | "machine" | "type" | "global".
    """
    if window is not None:
        wkey = f"{machine}|{window}"
        if wkey in baselines:
            return baselines[wkey], "machine+window"
    if machine in baselines:
        return baselines[machine], "machine"
    if machine_type:
        tkey = f"__type__{machine_type}"
        if tkey in baselines:
            return baselines[tkey], "type"
    return baselines["__global__"], "global"


def apply_zscore(df, baselines, feature_cols,
                 machine_col="machine", type_col="type", window_col="window"):
    """
    Return a z-scored copy of feature_cols.
    Each row uses its machine+window baseline (fallback chain above).
    Requires a 'window' column.
    """
    out  = df[feature_cols].copy().astype(float)
    glob = baselines["__global__"]
    has_type   = type_col in df.columns
    has_window = window_col in df.columns

    group_cols = [machine_col]
    if has_window:
        group_cols.append(window_col)

    for key, idx in df.groupby(group_cols).groups.items():
        if has_window:
            machine, window = key
        else:
            machine, window = key, None
        mtype = None
        if has_type:
            mtype = str(df.loc[idx, type_col].iloc[0])
        stats, _ = get_stats(baselines, str(machine),
                             window=window, machine_type=mtype)
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
