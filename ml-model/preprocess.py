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
  even though it is anomalous for 3am. Splitting the baseline by hour of day
  lets 0 mean "normal for this machine AT THIS TIME OF DAY".

Time windows: one per hour, "h00" (00:00-00:59) ... "h23" (23:00-23:59).
  Until 7 November 2026 the windows were 4 blocks of 6 hours (night 00-05,
  morning 06-11, afternoon 12-17, evening 18-23). A 6-hour block mixes hours of
  different load (morning goes from the night level to full load), which widens
  its std; the per-hour windows were chosen after measuring 1, 4, 8, 12 and 24
  windows with the served models on two independent sets of fleets
  (ml-model/test_baseline_variants.py, results in models/results/baseline_variants_*.json).
  Files built with the 4 blocks are still read correctly: window_function()
  picks the mapping from each file's own __windows__ metadata.

Used by trainers AND the FastAPI serving layer.
"""
import json
from pathlib import Path

import pandas as pd

STD_FLOOR = 1e-8   # flat metric (std=0) -> avoid division by zero

WINDOWS = tuple(f"h{h:02d}" for h in range(24))                 # current: one window per hour
LEGACY_WINDOWS = ("night", "morning", "afternoon", "evening")     # 4 x 6 hours, until 7 Nov 2026


def hour_to_window(hour: int) -> str:
    """Map an hour (0-23) to its time window: "h00" ... "h23"."""
    return f"h{int(hour):02d}"


def legacy_hour_to_window(hour: int) -> str:
    """The 4-window mapping of baselines files built before 7 November 2026."""
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def window_function(baselines):
    """The hour -> window mapping a baselines file was built with: per hour, or the
    legacy 4 windows. Read from the windows its machine|window KEYS actually use
    (the __windows__ metadata only when the file has no such key): a file whose
    metadata disagrees with its keys -- e.g. per-hour keys under the legacy list,
    written by test_baseline_variants.py before a9e17e9a -- is still read by its
    keys, not silently sent to the all-day fallback. None when the file has no
    windows or uses another grouping (then a frame's own 'window' column is used
    as it is)."""
    if not baselines.get("__has_windows__"):
        return None
    names = ({k.split("|", 1)[1] for k in baselines if "|" in k}
             or set(baselines.get("__windows__") or []))
    if names and names <= set(WINDOWS):           # a file may miss an hour it never saw
        return hour_to_window
    if names and names <= set(LEGACY_WINDOWS):
        return legacy_hour_to_window
    return None


def add_window_column(df, timestamp_col="timestamp"):
    """Add an integer 'hour' and string 'window' column derived from timestamp."""
    ts = pd.to_datetime(df[timestamp_col])
    df = df.copy()
    df["hour"] = ts.dt.hour
    df["window"] = df["hour"].map({h: hour_to_window(h) for h in range(24)})
    return df


def _mean_std(group, feature_cols):
    """{col: [mean, std]} of one group of rows; std (ddof=0) floored at STD_FLOOR."""
    out = {}
    for col in feature_cols:
        mean = float(group[col].mean())
        std  = float(group[col].std(ddof=0))
        out[col] = [mean, max(std, STD_FLOOR)]
    return out


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
      baselines["__windows__"]             = ["h00", ...]  # the windows present in df
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
        baselines[f"{machine}|{window}"] = _mean_std(group, feature_cols)

    # 2. Per-machine (any time) baselines — fallback if a window is missing
    for machine, group in df.groupby(machine_col):
        key = str(machine)
        baselines[key] = _mean_std(group, feature_cols)
        if type_col in df.columns:
            baselines[key]["__type__"] = str(group[type_col].iloc[0])

    # 3. Per-type baselines
    if type_col in df.columns:
        for mtype, group in df.groupby(type_col):
            baselines[f"__type__{mtype}"] = _mean_std(group, feature_cols)

    # 4. Global fallback
    baselines["__global__"] = {
        col: [float(df[col].mean()), float(max(df[col].std(ddof=0), 1.0))]
        for col in feature_cols
    }

    # 5. Metadata
    baselines["__feature_order__"] = list(feature_cols)
    present = set(df[window_col].dropna().astype(str))
    baselines["__windows__"]       = [w for w in WINDOWS if w in present] or sorted(present)
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
    Requires a 'window' column. When df also has an 'hour' column, the window
    is recomputed from it with the mapping THESE baselines were built with
    (window_function), so a frame can be scored against an older file (e.g. the
    production model during the switch to per-hour windows) without mismatch.
    """
    fn = window_function(baselines) if "hour" in df.columns else None
    if fn is not None and window_col in df.columns:
        df = df.assign(**{window_col: df["hour"].astype(int).map({h: fn(h) for h in range(24)})})
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


# --- Rolling / trend features (v2.13.0) ---
#
# For gradual-onset anomalies like memory leaks, the current single-timestamp
# z-score approach struggles: an early leak with ram=62 is only mildly elevated.
# Rolling features capture the pattern of change over recent history:
#
#   {col}_rolling_mean  -- avg of last N readings; establishes recent baseline
#   {col}_rolling_std   -- volatility over last N readings
#   {col}_delta         -- current - N-readings-ago; the rate of change
#
# Applied ONLY to metrics where change-over-time is diagnostic:
# cpu, ram, load_avg. Not applied to disk_usage (accumulates monotonically),
# disk_io (already noisy), or network (spikes are the norm).
#
# Rolling features are computed per-machine, ordered by timestamp. Because
# they need history, the first WINDOW-1 rows of each machine have a partial
# window: the rolling mean uses the rows available (min_periods=1), the rolling
# std and the delta are filled with 0.0 ("no change" -- the safest default
# when we lack history).

ROLLING_WINDOW_SIZE = 10  # 10 readings @ 30s = 5 minutes of history
ROLLING_FEATURE_BASE_COLS = ("cpu", "ram", "load_avg")


def rolling_feature_names(base_cols=ROLLING_FEATURE_BASE_COLS):
    """Return the list of new rolling-feature column names in a stable order."""
    names = []
    for col in base_cols:
        names.append(f"{col}_rolling_mean")
        names.append(f"{col}_rolling_std")
        names.append(f"{col}_delta")
    return names

def rolling_features_from_history(history, base_cols=ROLLING_FEATURE_BASE_COLS):
    """
    Rolling features for ONE reading from its explicit history window, in
    rolling_feature_names() order. history = {col: [v1..vN]}, last value =
    current reading. Used by the API (caller-supplied history) and by the
    trainer for feedback rows (history stored with the prediction).

    Identical to add_rolling_features() at a row with a full window:
      mean = window mean; std = sample std (ddof=1), 0.0 if < 2 values;
      delta = last - first (diff over window-1 periods).
    Checked by tests/test_api.py::test_rolling_features_match_training_pipeline.
    """
    import statistics
    feats = []
    for col in base_cols:
        vals = [float(v) for v in history[col]]
        feats.append(sum(vals) / len(vals))
        feats.append(statistics.stdev(vals) if len(vals) >= 2 else 0.0)
        feats.append(vals[-1] - vals[0])
    return feats

def add_rolling_features(df, machine_col="machine", timestamp_col="timestamp",
                         base_cols=ROLLING_FEATURE_BASE_COLS,
                         window=ROLLING_WINDOW_SIZE):
    """
    Add rolling mean/std/delta features per machine, ordered by timestamp.

    Returns a copy of the DataFrame with 9 new columns (3 features x 3 base cols).
    Undefined values (early rows per machine) are filled with 0.0.

    Real cost: O(N * window) with grouping overhead. For 17.28M rows this
    takes ~30-60 seconds; the returned DataFrame is ~40% larger in memory.
    """
    df = df.copy()
    df = df.sort_values([machine_col, timestamp_col]).reset_index(drop=True)

    for col in base_cols:
        # rolling_mean: average over the current row + previous (window-1) rows
        df[f"{col}_rolling_mean"] = (
            df.groupby(machine_col, sort=False)[col]
              .rolling(window=window, min_periods=1)
              .mean()
              .reset_index(level=0, drop=True)
        )
        # rolling_std: volatility of same window; min_periods=2 needed for std
        df[f"{col}_rolling_std"] = (
            df.groupby(machine_col, sort=False)[col]
              .rolling(window=window, min_periods=2)
              .std()
              .fillna(0.0)
              .reset_index(level=0, drop=True)
        )
        # delta: current value minus value from (window-1) rows ago
        df[f"{col}_delta"] = (
            df.groupby(machine_col, sort=False)[col]
              .diff(periods=window - 1)
              .fillna(0.0)
        )

    return df
