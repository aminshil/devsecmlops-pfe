"""
SMD (Server Machine Dataset) adapter.

Converts SMD's raw format into our CSV schema so train.py can consume it.

The 38 SMD features are anonymized. We support three loading strategies:
  --mode first3      : take the first 3 columns as cpu/ram/network
                       (apples-to-apples with our 3-feature synthetic POC)
  --mode top3        : take the 3 highest-variance columns
                       (feature-selection baseline)
  --mode all         : keep all non-constant columns
                       (best real-world performance)
"""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SMD_DIR = ROOT / "data" / "smd-raw" / "ServerMachineDataset"
OUTPUT = ROOT / "data" / "smd.csv"


def load_machine_raw(name: str) -> tuple[pd.DataFrame, pd.Series]:
    data_path = SMD_DIR / "test" / f"machine-{name}.txt"
    label_path = SMD_DIR / "test_label" / f"machine-{name}.txt"
    if not data_path.exists():
        raise FileNotFoundError(f"Machine not found: {data_path}")
    df = pd.read_csv(data_path, header=None)
    df.columns = [f"col_{i}" for i in range(len(df.columns))]
    labels = pd.read_csv(label_path, header=None, names=["label"])["label"].astype(int)
    return df, labels


def drop_constant_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = df.columns[df.var() > 0]
    dropped = [c for c in df.columns if c not in keep]
    if dropped:
        print(f"  Dropped {len(dropped)} constant columns: {dropped}")
    return df[keep]


def select_features(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    if mode == "first3":
        out = df.iloc[:, :3].copy()
        out.columns = ["cpu", "ram", "network"]
    elif mode == "top3":
        top = df.var().sort_values(ascending=False).head(3).index.tolist()
        print(f"  Top-3 by variance: {top}")
        out = df[top].copy()
        out.columns = ["cpu", "ram", "network"]
    elif mode == "all":
        out = drop_constant_columns(df).copy()
        renames = {out.columns[i]: name for i, name in enumerate(["cpu", "ram", "network"])}
        out = out.rename(columns=renames)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return out


def load_machine(name: str, mode: str) -> pd.DataFrame:
    raw, labels = load_machine_raw(name)
    df = select_features(raw, mode)
    df["label"] = labels.values
    start = datetime(2026, 1, 1, 0, 0, 0)
    df.insert(0, "timestamp", [start + timedelta(minutes=i) for i in range(len(df))])
    df.insert(1, "machine", name)
    return df


def list_machines() -> list[str]:
    return sorted(
        p.stem.replace("machine-", "")
        for p in (SMD_DIR / "test").glob("machine-*.txt")
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--machine", default="1-1", help="Machine ID (default: 1-1)")
    parser.add_argument("--all-machines", action="store_true", help="Load all 28 machines")
    parser.add_argument("--mode", choices=["first3", "top3", "all"], default="all",
                        help="Feature selection strategy (default: all)")
    parser.add_argument("--output", type=Path, default=OUTPUT,
                        help=f"Output CSV (default: {OUTPUT})")
    args = parser.parse_args()

    print(f"Mode: {args.mode}")
    if args.all_machines:
        machines = list_machines()
        print(f"Loading all {len(machines)} machines...")
        df = pd.concat([load_machine(m, args.mode) for m in machines], ignore_index=True)
    else:
        print(f"Loading machine-{args.machine}...")
        df = load_machine(args.machine, args.mode)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)

    n_features = len([c for c in df.columns if c not in ("timestamp", "machine", "label")])
    print(f"\nWrote {len(df):,} rows x {n_features} features to {args.output}")
    print(f"  Normal:  {(df['label'] == 0).sum():,}")
    print(f"  Anomaly: {(df['label'] == 1).sum():,}  ({df['label'].mean()*100:.2f}%)")


if __name__ == "__main__":
    main()
