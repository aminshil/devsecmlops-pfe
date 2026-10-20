#!/usr/bin/env python3
"""
How much of the headline score survives on machines the model has never seen?

The train, validation and test fleets share machine names and calendar (different
noise seeds only), so the headline F1 is measured on machines whose per-machine
baselines were learned in training. This script builds a FRESH fleet with its own
seed and re-scores the served model on four versions of it, at the manifest threshold
and with the served rule (classifier OR IsolationForest):

  control   original machine names (same machines as training, new noise): the reference
  A unseen  machines renamed, type kept       -> baselines fall back to the per-type average
  B unknown machines renamed, type unknown    -> fall back to the GLOBAL baseline (what the
                                                 API does for a machine it has never heard of)
  C shifted B plus a per-machine level shift (default +-15 %): a site whose machines simply
                                                 run hotter or cooler than the training fleet

Read-only: nothing under models/ or data/ is touched. The data are still simulated, so this
measures robustness to unseen machines and level shifts, not accuracy on real infrastructure.

  python scripts/new_site_eval.py --days 7 --out build/new_site_eval.json
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))

import train_production as tp  # noqa: E402

LEVEL_COLS = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]
PERCENT_COLS = ["cpu", "ram", "disk_io", "disk_usage"]


def generate(days: int, seed: int, path: Path) -> None:
    subprocess.run([sys.executable, str(ROOT / "ml-model" / "generate_telecom_fleet.py"),
                    "--days", str(days), "--seed", str(seed), "--output", str(path)],
                   check=True, stdout=subprocess.DEVNULL)


def rename(df: pd.DataFrame, keep_type: bool) -> pd.DataFrame:
    out = df.copy()
    out["machine"] = "site2-" + out["machine"].astype(str)
    if not keep_type:
        out["type"] = "unknown-type"
    return out


def level_shift(df: pd.DataFrame, spread: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = df.copy()
    factor = {m: float(np.clip(rng.normal(1.0, spread), 1 - 2 * spread, 1 + 2 * spread))
              for m in out["machine"].unique()}
    f = out["machine"].map(factor).to_numpy()
    for c in LEVEL_COLS:
        out[c] = out[c].to_numpy() * f
    for c in PERCENT_COLS:
        out[c] = out[c].clip(0, 100)
    return out


def score(csv: Path, threshold: float) -> dict:
    ev = tp.evaluate_set(tp.MODELS, tp.load_fleet(csv), threshold)
    v4 = ev["v4"]
    return {k: v4[k] for k in ("precision", "recall", "f1")} | {
        "classifier_only_f1": v4["classifier_only_f1"], "per_cause_recall": v4["per_cause_recall"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=777, help="must differ from the train/validation/test seeds")
    ap.add_argument("--shift", type=float, default=0.15, help="per-machine level shift spread for scenario C")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    manifest = tp.read_manifest(tp.MODELS)["production"]
    threshold = float(manifest["decision_threshold"])
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base = tmp / "fresh.csv"
        print(f"generating a fresh {a.days}-day fleet (seed {a.seed}) ...", file=sys.stderr)
        generate(a.days, a.seed, base)
        df = pd.read_csv(base)
        variants = {
            "control (same machines)": df,
            "A unseen names, type known": rename(df, keep_type=True),
            "B unseen names, type unknown": rename(df, keep_type=False),
            f"C B + level shift (+-{a.shift:.0%})": level_shift(rename(df, keep_type=False), a.shift, a.seed),
        }
        results = {}
        for name, frame in variants.items():
            path = tmp / "v.csv"
            frame.to_csv(path, index=False)
            print(f"scoring: {name}", file=sys.stderr)
            results[name] = score(path, threshold)

    print(f"\nserved v4 rule at threshold {threshold}, fresh fleet seed {a.seed}, {a.days} days, {len(df):,} rows\n")
    print(f"{'scenario':<34}{'precision':>10}{'recall':>9}{'F1':>8}{'clf-only F1':>13}")
    for name, r in results.items():
        print(f"{name:<34}{r['precision']:>10}{r['recall']:>9}{r['f1']:>8}{r['classifier_only_f1']:>13}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps({"threshold": threshold, "seed": a.seed, "days": a.days,
                                     "rows": int(len(df)), "scenarios": results}, indent=2))
        print(f"\nwritten to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
