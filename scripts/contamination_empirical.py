#!/usr/bin/env python3
"""EMPIRICAL contamination test: take the REAL judged rows from the feedback DB export, split them
into true anomalies vs true normals by the operator verdict, append them to the base training fleet,
and recompute contamination for real. No assumed 0.67 -- the judged pool's anomaly fraction is counted.

Verdict -> truth (same convention as ml-model/train_production.feedback_examples):
    true_positive, false_negative   -> real ANOMALY (label 1)
    false_positive, true_negative   -> real NORMAL  (label 0)

Steps on the VM:
    python3 scripts/export_feedback_dataset.py        # writes data/feedback/feedback_<ts>.csv
    python3 contamination_empirical.py                # reads them, prints the real numbers
Read-only; it changes nothing and trains nothing.
"""
import argparse
import glob
import sys
from pathlib import Path

ANOM = {"true_positive", "false_negative"}
NORM = {"false_positive", "true_negative"}


def base_rate(path: Path):
    try:
        import pandas as pd
    except ImportError:
        return None, 0
    if not path.exists():
        return None, 0
    total = anom = 0
    for chunk in pd.read_csv(path, usecols=["label"], chunksize=1_000_000):
        col = chunk["label"].astype(int)
        total += len(col); anom += int(col.sum())
    return (anom / total if total else None), total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, default=Path("data/telecom_fleet_v2_labeled.csv"))
    ap.add_argument("--feedback", default="data/feedback/feedback_*.csv",
                    help="glob of exported feedback CSVs")
    args = ap.parse_args()
    import pandas as pd

    files = sorted(glob.glob(args.feedback))
    if not files:
        print(f"No feedback files match {args.feedback!r}.")
        print("Run:  python3 scripts/export_feedback_dataset.py   (needs the DB env vars), then retry.")
        return 1
    fb = pd.concat([pd.read_csv(f) for f in files], ignore_index=True).drop_duplicates(subset="id", keep="last")
    v = fb["operator_verdict"].fillna("")
    counts = v.value_counts().to_dict()
    n_anom = int(v.isin(ANOM).sum())
    n_norm = int(v.isin(NORM).sum())
    n_other = len(fb) - n_anom - n_norm
    judged = n_anom + n_norm
    if judged == 0:
        print("Found feedback files but no usable verdicts in them.")
        return 1

    rate, base_n = base_rate(args.train)
    if rate is None:
        print(f"Could not read {args.train}; run this on the VM where the data lives.")
        return 1

    emp = n_anom / judged
    cont_A = (rate * base_n + n_anom) / (base_n + judged)                 # add judged rows as-is
    cont_B = (rate * (base_n + judged))  / (base_n + judged)              # same COUNT, representative
    print(f"Judged rows (real):         {judged:,}   from {len(files)} file(s)")
    print(f"  verdict breakdown:        {counts}")
    print(f"  -> real anomalies:        {n_anom:,}   (true_positive + false_negative)")
    print(f"  -> real normals:          {n_norm:,}   (false_positive + true_negative)")
    if n_other:
        print(f"  -> other/blank verdicts:  {n_other:,}   (ignored)")
    print()
    print(f"Empirical anomaly fraction of the judged pool: {emp:.4f}")
    print("  (this is the real precision-like number, measured, not assumed)")
    print()
    print(f"Base fleet:                 {base_n:,} rows at contamination {rate:.4f}")
    print(f"Case A  add judged as-is:   {cont_A:.6f}   (delta {cont_A - rate:+.6f})")
    print(f"Case B  same count, repr.:  {cont_B:.6f}   (delta {cont_B - rate:+.6f})")
    print()
    if judged < base_n * 0.001:
        print(f"At {judged:,} judged rows vs {base_n:,} base rows, the change is negligible either way:")
        print("the feedback is far too small to move the base rate. That is itself a reason contamination")
        print("is not recomputed from it. The bias only bites at scale (see contamination_ab.py's table),")
        print(f"and even then only for Case A, because the judged pool is {emp:.0%} anomalies, not {rate:.0%}.")
    else:
        print(f"Case A already pulls contamination up by {cont_A - rate:+.6f} because the judged pool is")
        print(f"{emp:.0%} anomalies ({rate:.0%} in the real fleet). Case B (representative) leaves it unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
