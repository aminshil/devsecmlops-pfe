"""
Full-grade trainer for the DevSecMLOps platform.

Differences vs. ml-model/train.py (the POC reference):
  * Train/test split (70/30), reported metrics are on the held-out test set.
  * Saves a JSON results file alongside the model artifact.
  * Logs F1, precision, recall, ROC-AUC, and the confusion matrix.
  * Unique experiment ID per run (timestamp + dataset stem).

Usage:
    python ml-model/train_full.py                                 # synthetic POC
    python ml-model/train_full.py --data data/smd_3feat.csv --contamination 0.0946
    python ml-model/train_full.py --data data/smd_all.csv  --contamination 0.0946
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "data" / "data.csv"
MODELS_DIR = ROOT / "models"
RESULTS_DIR = ROOT / "models" / "results"

META_COLS = {"timestamp", "machine", "label"}


def load(data_path: Path):
    df = pd.read_csv(data_path)
    feature_cols = [c for c in df.columns if c not in META_COLS]
    if not feature_cols:
        raise ValueError(f"No feature columns in {data_path}")
    if "label" not in df.columns:
        raise ValueError(f"Missing 'label' column in {data_path}")
    X = df[feature_cols]
    y = df["label"].astype(int)
    return X, y, feature_cols


def predict_binary(model: IsolationForest, X: pd.DataFrame) -> np.ndarray:
    """IsolationForest returns -1 for anomalies, 1 for normals; convert to 0/1."""
    return (model.predict(X) == -1).astype(int)


def anomaly_scores(model: IsolationForest, X: pd.DataFrame) -> np.ndarray:
    """Return per-sample anomaly score (higher = more anomalous).
    sklearn's score_samples returns higher = more normal, so we negate."""
    return -model.score_samples(X)


def evaluate(model: IsolationForest, X, y_true) -> dict:
    y_pred = predict_binary(model, X)
    scores = anomaly_scores(model, X)
    cm = confusion_matrix(y_true, y_pred).tolist()  # [[TN,FP],[FN,TP]]
    return {
        "f1": float(f1_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred)),
        "roc_auc": float(roc_auc_score(y_true, scores)) if y_true.nunique() > 1 else None,
        "confusion_matrix": cm,
        "n_samples": int(len(y_true)),
        "n_anomalies": int(y_true.sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--n-estimators", type=int, default=100)
    parser.add_argument("--max-samples", default="auto",
                        help="IsolationForest max_samples (default: auto)")
    parser.add_argument("--test-size", type=float, default=0.3,
                        help="Test set fraction (default: 0.3)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tag", type=str, default=None,
                        help="Optional run tag for the experiment ID")
    args = parser.parse_args()

    # Experiment ID
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    exp_id = f"{timestamp}_{args.data.stem}{tag}"
    print(f"Experiment ID: {exp_id}")

    # Load
    print(f"\nLoading {args.data}")
    X, y, features = load(args.data)
    print(f"  {len(X):,} rows x {len(features)} features")
    print(f"  Anomaly ratio (full): {y.mean()*100:.2f}%")

    # Train / test split (stratified so both halves have anomalies)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=args.test_size,
        random_state=args.seed,
        stratify=y,
    )
    print(f"  Train: {len(X_train):,}  Test: {len(X_test):,}")

    # Try parse max_samples as int, else keep string ("auto")
    try:
        max_samples = int(args.max_samples)
    except ValueError:
        max_samples = args.max_samples

    # Train
    print(f"\nTraining IsolationForest(contamination={args.contamination}, "
          f"n_estimators={args.n_estimators}, max_samples={max_samples})")
    model = IsolationForest(
        contamination=args.contamination,
        n_estimators=args.n_estimators,
        max_samples=max_samples,
        random_state=args.seed,
        n_jobs=-1,
    )
    model.fit(X_train)

    # Evaluate on held-out test set
    metrics_test = evaluate(model, X_test, y_test)
    metrics_train = evaluate(model, X_train, y_train)

    print("\n=== Test set (held-out) ===")
    print(f"F1-score:  {metrics_test['f1']:.3f}")
    print(f"Precision: {metrics_test['precision']:.3f}")
    print(f"Recall:    {metrics_test['recall']:.3f}")
    if metrics_test["roc_auc"] is not None:
        print(f"ROC-AUC:   {metrics_test['roc_auc']:.3f}")
    cm = metrics_test["confusion_matrix"]
    print(f"\nConfusion matrix (rows=true, cols=pred):")
    print(f"               pred normal   pred anomaly")
    print(f"  true normal      {cm[0][0]:>7}      {cm[0][1]:>7}")
    print(f"  true anomaly     {cm[1][0]:>7}      {cm[1][1]:>7}")

    print(f"\nClassification report (test):")
    y_pred_test = predict_binary(model, X_test)
    print(classification_report(y_test, y_pred_test, target_names=["normal", "anomaly"]))

    # Save model + results
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / f"model_{exp_id}.pkl"
    results_path = RESULTS_DIR / f"{exp_id}.json"

    joblib.dump(model, model_path)

    results = {
        "experiment_id": exp_id,
        "timestamp": timestamp,
        "data": str(args.data.resolve().relative_to(ROOT)),
        "model": "IsolationForest",
        "hyperparameters": {
            "contamination": args.contamination,
            "n_estimators": args.n_estimators,
            "max_samples": str(max_samples),
            "random_state": args.seed,
        },
        "dataset": {
            "total_samples": int(len(X)),
            "features": features,
            "n_features": len(features),
            "anomaly_ratio": float(y.mean()),
            "train_size": int(len(X_train)),
            "test_size": int(len(X_test)),
        },
        "metrics_test": metrics_test,
        "metrics_train": metrics_train,
    }
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nModel:   {model_path}")
    print(f"Results: {results_path}")


if __name__ == "__main__":
    main()
