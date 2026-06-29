"""
Build one multi-machine SMD CSV for the benchmark.
Keeps ALL 38 metrics (aligned across machines), a synthetic per-minute
timestamp per machine (so time features have signal), machine id, and label.
Drops only columns constant across the WHOLE pool. Output: data/smd_multi.csv
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_smd import load_machine_raw, list_machines

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "smd_multi.csv"


def main():
    machines = list_machines()
    print(f"Loading {len(machines)} machines (all 38 metrics)... this writes a biggish CSV, give it a moment.")
    start = datetime(2026, 1, 1)
    frames = []
    for name in machines:
        raw, labels = load_machine_raw(name)            # cols col_0..col_37
        raw = raw.copy()
        raw.insert(0, "timestamp", [start + timedelta(minutes=i) for i in range(len(raw))])
        raw.insert(1, "machine", name)
        raw["label"] = labels.values
        frames.append(raw)
    df = pd.concat(frames, ignore_index=True)

    feat = [c for c in df.columns if c.startswith("col_")]
    keep = [c for c in feat if df[c].var() > 0]
    dropped = [c for c in feat if c not in keep]
    if dropped:
        print(f"Dropped {len(dropped)} globally-constant cols: {dropped}")
    df = df[["timestamp", "machine"] + keep + ["label"]]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT, index=False)
    print(f"\nWrote {len(df):,} rows x {len(keep)} features to {OUTPUT}")
    print(f"  Machines: {df['machine'].nunique()}   Anomaly ratio: {df['label'].mean()*100:.2f}%")


if __name__ == "__main__":
    main()
