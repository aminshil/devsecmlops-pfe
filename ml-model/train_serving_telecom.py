"""
Trains the Telecom serving artifact using per-machine PER-TIME-WINDOW
z-scored features across all 200 telecom machines on the full fleet dataset.

Decision record:
  - Time features as explicit columns (hour_sin/cos): tested, REJECTED (F1 0.640 -> 0.616)
  - Per-time-window baselines (this approach): tested on full fleet, ADOPTED
    A) per-machine        F1@thr=0.6434  ROC-AUC=0.9066
    C) per-time-window    F1@thr=0.6475  ROC-AUC=0.9091  <-- shipped
  Per-time-window catches subtle time-dependent anomalies (e.g. 50% CPU at 3am
  on a machine that idles at 30% at night) that a single all-day baseline misses.

Outputs:
  models/telecom_serving_model.pkl
  models/telecom_serving_baselines.json
"""
import sys
from pathlib import Path

import joblib
import pandas as pd
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

print(f"\n  F1        : {f1_score(y_true, y_pred):.3f}")
print(f"  Precision : {precision_score(y_true, y_pred):.3f}")
print(f"  Recall    : {recall_score(y_true, y_pred):.3f}")
print(f"  ROC-AUC   : {roc_auc_score(y_true, y_score):.3f}")

MODELS.mkdir(parents=True, exist_ok=True)
joblib.dump(model, MODELS / "telecom_serving_model.pkl")
save_baselines(baselines, MODELS / "telecom_serving_baselines.json")

print(f"\nSaved: {MODELS / 'telecom_serving_model.pkl'}")
print(f"Saved: {MODELS / 'telecom_serving_baselines.json'}")
print(f"Machines in artifact: {n_machine}  |  window baselines: {n_window}")
