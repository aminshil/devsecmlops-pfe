"""
Trains the Telecom serving artifact: one Isolation Forest over per-machine
z-scored features across all 200 telecom machines on the full fleet dataset.

Outputs:
  models/telecom_serving_model.pkl
  models/telecom_serving_baselines.json
"""
import json
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import build_baselines, apply_zscore, save_baselines

ROOT   = Path(__file__).resolve().parent.parent
DATA   = ROOT / "data" / "telecom_fleet.csv"
MODELS = ROOT / "models"
META_COLS = {"timestamp", "machine", "label", "type"}

print(f"Loading {DATA} ...")
df = pd.read_csv(DATA)
features = [c for c in df.columns if c not in META_COLS]
print(f"  {len(df):,} rows  x  {len(features)} features  x  {df['machine'].nunique()} machines")
print(f"  Anomaly ratio : {df['label'].mean()*100:.2f}%")
print(f"  Machine types : {sorted(df['type'].unique())}")

df_tr, df_te = train_test_split(df, test_size=0.3, random_state=42, stratify=df["label"])
print(f"  Train: {len(df_tr):,}  Test: {len(df_te):,}\n")

print("Building per-machine baselines on TRAIN split only ...")
baselines = build_baselines(df_tr, features)
baselines["__feature_order__"] = features

# Store machine type in baselines for API enrichment
for machine, grp in df.groupby("machine"):
    if str(machine) in baselines:
        baselines[str(machine)]["__type__"] = grp["type"].iloc[0]

print("Training IsolationForest (contamination=0.068, n_estimators=200) ...")
model = IsolationForest(contamination=0.068, n_estimators=200,
                        random_state=42, n_jobs=-1)
model.fit(apply_zscore(df_tr, baselines, features))

y_pred  = (model.predict(apply_zscore(df_te, baselines, features)) == -1).astype(int)
y_score = -model.score_samples(apply_zscore(df_te, baselines, features))
y_true  = df_te["label"]

f1      = f1_score(y_true, y_pred)
prec    = precision_score(y_true, y_pred)
rec     = recall_score(y_true, y_pred)
roc     = roc_auc_score(y_true, y_score)

print(f"\n  F1        : {f1:.3f}")
print(f"  Precision : {prec:.3f}")
print(f"  Recall    : {rec:.3f}")
print(f"  ROC-AUC   : {roc:.3f}")

MODELS.mkdir(parents=True, exist_ok=True)
joblib.dump(model, MODELS / "telecom_serving_model.pkl")
save_baselines(baselines, MODELS / "telecom_serving_baselines.json")

print(f"\nSaved: {MODELS / 'telecom_serving_model.pkl'}")
print(f"Saved: {MODELS / 'telecom_serving_baselines.json'}")
print(f"Machines in artifact: {sum(1 for m in baselines if not m.startswith('__'))}")
