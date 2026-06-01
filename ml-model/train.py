"""
Train an Isolation Forest anomaly detector on the synthetic POC data.
Output: models/model.pkl + printed evaluation metrics.
"""
import joblib
import pandas as pd
from pathlib import Path
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, f1_score, precision_score, recall_score

# --- Paths ---
ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "data.csv"
MODEL_PATH = ROOT / "models" / "model.pkl"

# --- Load data ---
df = pd.read_csv(DATA_PATH)
print(f"Loaded {len(df)} rows from {DATA_PATH}")

X = df[["cpu", "ram", "network"]]
y_true = df["label"]

# --- Train ---
model = IsolationForest(
    contamination=0.05,
    n_estimators=100,
    random_state=42,
)
model.fit(X)

# --- Predict ---
# IsolationForest returns: -1 = anomaly, 1 = normal
# Convert to our label format: 1 = anomaly, 0 = normal
y_pred_raw = model.predict(X)
y_pred = (y_pred_raw == -1).astype(int)

# --- Evaluate ---
f1 = f1_score(y_true, y_pred)
precision = precision_score(y_true, y_pred)
recall = recall_score(y_true, y_pred)

print("\n=== Evaluation ===")
print(f"F1-score:  {f1:.3f}")
print(f"Precision: {precision:.3f}")
print(f"Recall:    {recall:.3f}")
print("\nDetailed report:")
print(classification_report(y_true, y_pred, target_names=["normal", "anomaly"]))

# --- Save ---
MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
joblib.dump(model, MODEL_PATH)
print(f"\nModel saved to {MODEL_PATH}")
