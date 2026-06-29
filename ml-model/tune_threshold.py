"""
Threshold (contamination) sweep for Isolation Forest.

The IsolationForest 'contamination' parameter controls the proportion of
samples flagged as anomalies. Setting it to the dataset's true anomaly
rate is intuitive but often suboptimal for F1: too low misses anomalies,
too high floods the alert system with false positives.

This script sweeps contamination from --min to --max in --steps increments,
trains a model at each point on the same train split, and reports the F1
on the held-out test set. The best contamination is highlighted.

Usage:
    python ml-model/tune_threshold.py --data data/smd_3feat.csv
    python ml-model/tune_threshold.py --data data/smd_all.csv --min 0.01 --max 0.25 --steps 25
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data" / "data.csv"
RESULTS_DIR = ROOT / "models" / "results"

META_COLS = {"timestamp", "machine", "label"}


def load(data_path: Path):
    df = pd.read_csv(data_path)
    features = [c for c in df.columns if c not in META_COLS]
    X = df[features]
    y = df["label"].astype(int)
    return X, y, features


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--min", type=float, default=0.01)
    parser.add_argument("--max", type=float, default=0.20)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--n-estimators", type=int, default=100)
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print(f"Loading {args.data}")
    X, y, features = load(args.data)
    print(f"  {len(X):,} rows x {len(features)} features")
    print(f"  True anomaly ratio: {y.mean()*100:.2f}%")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=args.seed, stratify=y,
    )
    print(f"  Train: {len(X_train):,}  Test: {len(X_test):,}\n")

    contaminations = np.linspace(args.min, args.max, args.steps)

    print(f"{'contamination':>14}  {'F1':>6}  {'precision':>10}  {'recall':>8}  {'ROC-AUC':>8}")
    print("-" * 60)

    results = []
    best = None

    for c in contaminations:
        model = IsolationForest(
            contamination=float(c),
            n_estimators=args.n_estimators,
            random_state=args.seed,
            n_jobs=-1,
        )
        model.fit(X_train)
        y_pred = (model.predict(X_test) == -1).astype(int)
        scores = -model.score_samples(X_test)

        f1 = f1_score(y_test, y_pred, zero_division=0)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec = recall_score(y_test, y_pred, zero_division=0)
        auc = roc_auc_score(y_test, scores)

        row = {"contamination": float(c), "f1": float(f1),
               "precision": float(prec), "recall": float(rec), "roc_auc": float(auc)}
        results.append(row)

        marker = ""
        if best is None or f1 > best["f1"]:
            best = row
            marker = "  <-- new best"

        print(f"{c:>14.4f}  {f1:>6.3f}  {prec:>10.3f}  {rec:>8.3f}  {auc:>8.3f}{marker}")

    print("-" * 60)
    print(f"\nBest: contamination={best['contamination']:.4f}  "
          f"F1={best['f1']:.3f}  precision={best['precision']:.3f}  recall={best['recall']:.3f}")

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"sweep_{timestamp}_{args.data.stem}.json"
    with open(out_path, "w") as f:
        json.dump({
            "data": str(args.data.resolve().relative_to(ROOT)),
            "n_features": len(features),
            "true_anomaly_ratio": float(y.mean()),
            "sweep": results,
            "best": best,
        }, f, indent=2)
    print(f"\nSaved sweep to {out_path}")


if __name__ == "__main__":
    main()
