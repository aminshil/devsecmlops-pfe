"""
Trains the Telecom serving artifact using per-machine PER-TIME-WINDOW
z-scored features across all 200 telecom machines on the full fleet dataset.

Decision record:
  - Time features as explicit columns (hour_sin/cos): tested, REJECTED (adds redundancy)
  - Per-time-window baselines (this approach): tested on full fleet, ADOPTED

  Final v2.3.0 (6 features, per-time-window):
      F1 = 0.648   Precision = 0.646   Recall = 0.650   ROC-AUC = 0.924

  Per-time-window catches subtle time-dependent anomalies (e.g. 50% CPU at 3am
  on a machine that idles at 30% at night) that a single all-day baseline misses.
  See test_timewindow_full.py for the tuned-threshold comparison that motivated
  choosing C over per-machine baselines.

Outputs:
  models/telecom_serving_model.pkl
  models/telecom_serving_baselines.json
"""
import sys
from pathlib import Path

import joblib
import pandas as pd
import mlflow
import mlflow.sklearn
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import (build_baselines, apply_zscore, save_baselines,
                        add_window_column)

ROOT   = Path(__file__).resolve().parent.parent
DATA   = ROOT / "data" / "telecom_fleet.csv"
MODELS = ROOT / "models"
META_COLS = {"timestamp", "machine", "label", "type", "hour", "window"}

mlflow.set_tracking_uri("http://localhost:5001")
mlflow.set_experiment("telecom-anomaly-detection")
mlflow.start_run()

print(f"Loading {DATA} ...")
df = pd.read_csv(DATA)
df = add_window_column(df)                       # adds 'hour' + 'window'
features = [c for c in df.columns if c not in META_COLS]
print(f"  {len(df):,} rows  x  {len(features)} features  x  {df['machine'].nunique()} machines")
print(f"  Features      : {features}")
print(f"  Anomaly ratio : {df['label'].mean()*100:.2f}%")
print(f"  Machine types : {sorted(df['type'].unique())}")
print(f"  Time windows  : {sorted(df['window'].unique())}")

df_tr, df_te = train_test_split(df, test_size=0.3, random_state=42, stratify=df["label"])
print(f"  Train: {len(df_tr):,}  Test: {len(df_te):,}\n")

print("Building per-machine per-window baselines on TRAIN split only ...")
baselines = build_baselines(df_tr, features)
n_window  = sum(1 for k in baselines if "|" in k)
n_machine = sum(1 for k in baselines
                if not k.startswith("__") and "|" not in k)
print(f"  window baselines : {n_window}")
print(f"  machine fallback : {n_machine}")

print("Training IsolationForest (contamination=0.068, n_estimators=200) ...")
model = IsolationForest(contamination=0.068, n_estimators=200,
                        random_state=42, n_jobs=-1)
model.fit(apply_zscore(df_tr, baselines, features))

y_pred  = (model.predict(apply_zscore(df_te, baselines, features)) == -1).astype(int)
y_score = -model.score_samples(apply_zscore(df_te, baselines, features))
y_true  = df_te["label"]

f1  = f1_score(y_true, y_pred)
prec = precision_score(y_true, y_pred)
rec  = recall_score(y_true, y_pred)
auc  = roc_auc_score(y_true, y_score)

print(f"\n  F1        : {f1:.3f}")
print(f"  Precision : {prec:.3f}")
print(f"  Recall    : {rec:.3f}")
print(f"  ROC-AUC   : {auc:.3f}")

mlflow.log_param("contamination", 0.068)
mlflow.log_param("n_estimators", 200)
mlflow.log_param("n_features", len(features))
mlflow.log_param("n_machines", df["machine"].nunique())
mlflow.log_param("resolution_seconds", 30)
mlflow.log_metric("f1", f1)
mlflow.log_metric("precision", prec)
mlflow.log_metric("recall", rec)
mlflow.log_metric("roc_auc", auc)

MODELS.mkdir(parents=True, exist_ok=True)
joblib.dump(model, MODELS / "telecom_serving_model.pkl")
save_baselines(baselines, MODELS / "telecom_serving_baselines.json")

mlflow.sklearn.log_model(model, "model", registered_model_name="telecom-anomaly-model")
mlflow.log_artifact(str(MODELS / "telecom_serving_baselines.json"))

print(f"\nSaved: {MODELS / 'telecom_serving_model.pkl'}")
print(f"Saved: {MODELS / 'telecom_serving_baselines.json'}")
print(f"Machines in artifact: {n_machine}  |  window baselines: {n_window}")
print("Logged to MLflow: http://localhost:5001")

mlflow.end_run()
