#!/usr/bin/env python3
"""
Re-measure the baseline choices with the SERVED decision (v4 XGBoost OR IsolationForest).

Why: the README's time-window evidence (test_timewindow_full.py, 6 July 2026) was measured with an
IsolationForest on a 400k-row sample of the v1 dataset; per-hour baselines and baselines without the
anomalies were never measured with the current models. This script measures them the way production
trains and scores a model, changing ONLY how the baselines are built:

  allday       one baseline per machine, all hours together
  windows4     4 windows of 6 hours (night, morning, afternoon, evening): what production serves
  hourly24     24 windows, one per hour
  trimmed      4 windows, top and bottom 5 % of each group dropped (preprocess_robust.py)
  normal_only  4 windows, baselines from label-0 rows only. Uses the labels, which a real operator
               does not have: an upper bound on what removing the anomalies could gain, not a
               deployable option.

For every variant, identically: train_production's training matrices (all rows, no feedback rows),
its XGB/IsolationForest parameters and seeds, its threshold rule on the validation fleet
(config/model_policy.json), and its served-decision scoring on the test fleet. Production files,
the manifest and MLflow are not touched: artifacts go to --work-dir.

Usage (needs about 30 GB of RAM for the full fleets; run it on the test machine, not the VM):
    python3 ml-model/test_baseline_variants.py
    python3 ml-model/test_baseline_variants.py --variants windows4 hourly24
Results: printed, and written after each variant to --out (models/results/ by default).
"""
import argparse
import gc
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import preprocess  # noqa: E402
import preprocess_robust  # noqa: E402
import train_production as tp  # noqa: E402

VARIANTS = ["allday", "windows4", "hourly24", "trimmed", "normal_only"]
DATA = ROOT / "data"


def set_windows(df, kind: str, original):
    """Give every row the window label this variant uses (the 'window' column drives the baselines'
    grouping and the z-score lookup alike, in training and in scoring)."""
    if kind == "allday":
        df["window"] = "all"
    elif kind == "hourly24":
        df["window"] = "h" + df["hour"].astype(int).map("{:02d}".format)
    else:
        df["window"] = original
    return df


def build(kind: str, train):
    if kind == "trimmed":
        return preprocess_robust.build_baselines(train, tp.FEATURES)
    if kind == "normal_only":
        return preprocess.build_baselines(train[train["label"] == 0], tp.FEATURES)
    return preprocess.build_baselines(train, tp.FEATURES)


def run_variant(kind, train, val, test, val_fp, work_dir: Path, originals) -> dict:
    t0 = time.time()
    for df, name in ((train, "train"), (val, "val"), (test, "test")):
        set_windows(df, kind, originals[name])
    baselines = build(kind, train)
    keys = sum(1 for k in baselines if "|" in k)
    tp.log(f"[{kind}] baselines: {len(baselines):,} keys ({keys:,} machine|window)")

    x_iso, x6, x15, y, n_norm = tp.training_matrices(train, baselines, None)
    contamination = float(train["label"].mean())
    art = work_dir / kind
    art.mkdir(parents=True, exist_ok=True)
    tp.train_candidate(x6, y, x15, y, x_iso, contamination, baselines, art)
    del x_iso, x6, x15, y
    gc.collect()

    sel = tp.select_candidate_threshold(art, val, val_fp)
    th = sel["selected"]["threshold"]
    tp.log(f"[{kind}] threshold on validation: {th} ({sel['rule']}): {sel['selected']}")
    ev = tp.evaluate_set(art, test, th)
    v4, v3 = ev["v4"], ev["v3"]
    tp.log(f"[{kind}] TEST served v4 F1={v4['f1']} P={v4['precision']} R={v4['recall']}  "
           f"v3 F1={v3['f1']}  ({time.time() - t0:.0f} s)")
    return {
        "variant": kind,
        "baseline_keys": len(baselines),
        "machine_window_keys": keys,
        "contamination": round(contamination, 5),
        "threshold": th,
        "validation_at_threshold": sel["selected"],
        "test_v4_served": {k: v4[k] for k in ("f1", "precision", "recall",
                                               "classifier_only_f1", "classifier_only_precision",
                                               "classifier_only_recall", "cause_accuracy")},
        "test_v4_per_cause_recall": v4["per_cause_recall"],
        "test_v3_served": {k: v3[k] for k in ("f1", "precision", "recall")},
        "seconds": round(time.time() - t0),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--train", type=Path, default=DATA / "telecom_fleet_v2_labeled.csv")
    ap.add_argument("--val", type=Path, default=DATA / "telecom_fleet_v2_val.csv")
    ap.add_argument("--test", type=Path, default=DATA / "telecom_fleet_v2_test.csv")
    ap.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    ap.add_argument("--work-dir", type=Path, default=ROOT / "build" / "baseline_variants")
    ap.add_argument("--out", type=Path, default=None,
                    help="results JSON (default: models/results/baseline_variants_<UTC time>.json)")
    args = ap.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = args.out or ROOT / "models" / "results" / f"baseline_variants_{stamp}.json"
    record = {
        "script": "ml-model/test_baseline_variants.py",
        "git_commit": tp.git_commit(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "datasets": {n: {"file": str(p), "sha256": tp.sha256(p)}
                     for n, p in (("train", args.train), ("val", args.val), ("test", args.test))},
        "note": "no feedback rows; same data, parameters, threshold rule and scoring for every variant",
        "results": [],
    }
    tp.log("loading the three fleets once ...")
    train, val, test = tp.load_fleet(args.train), tp.load_fleet(args.val), tp.load_fleet(args.test)
    for n, df in (("train", train), ("val", val), ("test", test)):
        record["datasets"][n]["rows"] = len(df)
    val_fp = {"file": str(args.val), "sha256": record["datasets"]["val"]["sha256"], "rows": len(val)}
    originals = {"train": train["window"].copy(), "val": val["window"].copy(), "test": test["window"].copy()}

    for kind in args.variants:
        record["results"].append(run_variant(kind, train, val, test, val_fp, args.work_dir, originals))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2) + "\n")
        tp.log(f"written {out}")

    print("\nvariant        keys   threshold  test F1   precision  recall   v3 F1")
    for r in record["results"]:
        t = r["test_v4_served"]
        print(f"{r['variant']:<13} {r['baseline_keys']:>6}   {r['threshold']:<9}  {t['f1']:<8}  "
              f"{t['precision']:<9}  {t['recall']:<7}  {r['test_v3_served']['f1']}")
    print("\nper-cause recall (v4 served, test):")
    causes = sorted({c for r in record["results"] for c in r["test_v4_per_cause_recall"]})
    print("variant        " + "  ".join(f"{c[:14]:<14}" for c in causes))
    for r in record["results"]:
        print(f"{r['variant']:<13}  " + "  ".join(f"{r['test_v4_per_cause_recall'].get(c, float('nan')):<14}"
                                                   for c in causes))
    return 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
