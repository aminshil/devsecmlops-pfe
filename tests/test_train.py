"""
Smoke + quality tests for the training pipeline.
These run after every commit (later via Jenkins) to make sure
the model still meets the success-metric targets.
"""
import subprocess
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "data.csv"
MODEL_PATH = ROOT / "models" / "model.pkl"

# Success-metric thresholds (from the Restitution 1 deck)
F1_TARGET = 0.85
PRECISION_MIN = 0.80
RECALL_MIN = 0.80


def _ensure_data_exists():
    """Regenerate data if missing — keeps the test self-contained."""
    if not DATA_PATH.exists():
        subprocess.run(
            [sys.executable, str(ROOT / "ml-model" / "generate_data.py")],
            check=True,
        )


def _ensure_model_exists():
    """Retrain if model is missing — same idea."""
    if not MODEL_PATH.exists():
        subprocess.run(
            [sys.executable, str(ROOT / "ml-model" / "train.py")],
            check=True,
        )


# ---------------------------------------------------------------
# Tests
# ---------------------------------------------------------------
def test_data_file_exists():
    """The CSV must be generated and non-empty."""
    _ensure_data_exists()
    assert DATA_PATH.exists(), f"Missing data file: {DATA_PATH}"
    df = pd.read_csv(DATA_PATH)
    assert len(df) >= 1000, f"Expected >=1000 rows, got {len(df)}"


def test_data_schema():
    """Required columns must be present and correctly typed."""
    df = pd.read_csv(DATA_PATH)
    expected = {"timestamp", "cpu", "ram", "network", "label"}
    assert expected.issubset(df.columns), f"Missing columns: {expected - set(df.columns)}"
    assert df["label"].isin([0, 1]).all(), "Labels must be 0 or 1"


def test_anomaly_ratio_reasonable():
    """Anomaly ratio should be close to the 5% target."""
    df = pd.read_csv(DATA_PATH)
    ratio = df["label"].mean()
    assert 0.03 <= ratio <= 0.08, f"Anomaly ratio {ratio:.3f} outside [0.03, 0.08]"


def test_model_file_exists():
    """The trained model must be saved by train.py."""
    _ensure_model_exists()
    assert MODEL_PATH.exists(), f"Missing model file: {MODEL_PATH}"


def test_model_is_isolation_forest():
    """The saved model must be an IsolationForest instance."""
    model = joblib.load(MODEL_PATH)
    assert isinstance(model, IsolationForest), f"Unexpected model type: {type(model)}"


def test_f1_meets_target():
    """F1-score on the training data must be >= 0.85 (success metric)."""
    df = pd.read_csv(DATA_PATH)
    model = joblib.load(MODEL_PATH)

    X = df[["cpu", "ram", "network"]]
    y_true = df["label"]
    y_pred = (model.predict(X) == -1).astype(int)

    f1 = f1_score(y_true, y_pred)
    precision = precision_score(y_true, y_pred)
    recall = recall_score(y_true, y_pred)

    print(f"\nF1={f1:.3f}  precision={precision:.3f}  recall={recall:.3f}")

    assert f1 >= F1_TARGET, f"F1 {f1:.3f} below target {F1_TARGET}"
    assert precision >= PRECISION_MIN, f"Precision {precision:.3f} below {PRECISION_MIN}"
    assert recall >= RECALL_MIN, f"Recall {recall:.3f} below {RECALL_MIN}"
