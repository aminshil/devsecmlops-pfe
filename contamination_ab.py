#!/usr/bin/env python3
"""Case A vs Case B: what happens to the IsolationForest's contamination when judged rows are
added to the training data.

  Case A  the judged rows are FLAGGED rows only (what the Review tab collects). Their anomaly
          fraction equals the detector's PRECISION (~0.67), because precision = TP/(TP+FP) is
          exactly 'of the readings we flagged, how many were truly anomalies'. Folding them into
          contamination drags it up toward 0.67 -> the net over-flags.

  Case B  the judged rows are REPRESENTATIVE (anomalies AND normals in their true proportion).
          Their anomaly fraction equals the base rate -> contamination stays put.

Read-only. Computes the real base rate from the training file; the rest is exact arithmetic.
    python3 contamination_ab.py
    python3 contamination_ab.py --precision 0.6713 --train data/telecom_fleet_v2_labeled.csv
"""
import argparse
import sys
from pathlib import Path

BASE_RATE_FALLBACK = 0.0675      # manifest iso.contamination, if the CSV is not present
PRECISION_DEFAULT = 0.6713       # served decision (classifier OR IsolationForest), test set @0.91


def base_rate_from_csv(path: Path) -> float | None:
    """Anomaly fraction = mean of the 'label' column, read in chunks so 17.28M rows fit in memory."""
    try:
        import pandas as pd
    except ImportError:
        return None
    if not path.exists():
        return None
    total = anom = 0
    for chunk in pd.read_csv(path, usecols=["label"], chunksize=1_000_000):
        col = chunk["label"].astype(int)
        total += len(col)
        anom += int(col.sum())
    return anom / total if total else None


def contamination_after(base_rate: float, base_n: int, added_n: int, added_rate: float) -> float:
    """Base pool (base_n rows at base_rate) + added_n judged rows at added_rate -> new overall rate."""
    anom = base_rate * base_n + added_rate * added_n
    return anom / (base_n + added_n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, default=Path("data/telecom_fleet_v2_labeled.csv"))
    ap.add_argument("--precision", type=float, default=PRECISION_DEFAULT,
                    help="anomaly fraction of the flagged/judged pool (Case A)")
    ap.add_argument("--base-rows", type=int, default=17_280_000,
                    help="size of the base training fleet, for the mixing ratio")
    args = ap.parse_args()

    rate = base_rate_from_csv(args.train)
    where = f"measured from {args.train}"
    if rate is None:
        rate, where = BASE_RATE_FALLBACK, f"fallback (could not read {args.train}; using manifest value)"

    print(f"Base anomaly rate (true contamination): {rate:.4f}   [{where}]")
    print(f"Base training fleet size:               {args.base_rows:,} rows")
    print(f"Flagged-pool anomaly fraction (Case A): {args.precision:.4f}   [= detector precision]")
    print()
    print(f"{'judged rows added':>18} | {'Case A (flagged only)':>22} | {'Case B (representative)':>24}")
    print("-" * 72)
    for added in (0, 1_000, 10_000, 50_000, 100_000, 500_000, 1_000_000):
        a = contamination_after(rate, args.base_rows, added, args.precision)
        b = contamination_after(rate, args.base_rows, added, rate)
        flag_a = "  <- inflating" if a > rate * 1.02 else ""
        print(f"{added:>18,} | {a:>22.4f} | {b:>24.4f}{flag_a}")
    print()
    print("Case A pulls contamination toward the precision (~0.67): the net would flag far more and")
    print("drown precision. Case B keeps it at the true base rate: representative data is safe to add.")
    print("This is why feedback (flagged rows) is sent ONLY to the classifier, and contamination stays")
    print("tied to the base fleet. To grow the net safely you add representative rows (Case B), which in")
    print("production means a RANDOM sample of unflagged readings as pseudo-normal, not the judged alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
