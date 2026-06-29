"""
Trains the SMD serving artifact: one Isolation Forest over per-machine z-scored
features across all 28 SMD machines and all 37 metrics on the full 708K rows.

Saves the feature column order in the baselines file under "__feature_order__"
so the API serves rows in the exact order the model was trained on.

Outputs:
  models/smd_serving_model.pkl
  models/smd_serving_baselines.json
"""
import json
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import build_baselines, apply_zscore, save_baselines

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "smd_multi.csv"
MODELS = ROOT / "models"
META_COLS = {"timestamp", "machine", "label"}

print(f"Loading {DATA} ...")
df = pd.read_csv(DATA)
features = [c for c in df.columns if c not in META_COLS]
print(f"  {len(df):,} rows  x  {len(features)} features  x  {df['machine'].nunique()} machines")
print(f"  Anomaly ratio: {df['label'].mean()*100:.2f}%")
print(f"  Feature order (first 5): {features[:5]} ... ({len(features)} total)")

df_tr, df_te = train_test_split(df, test_size=0.3, random_state=42,
                                stratify=df["label"])
print(f"  Train: {len(df_tr):,}  Test: {len(df_te):,}\n")

print("Building per-machine baselines on the TRAIN split only...")
baselines = build_baselines(df_tr, features)
baselines["__feature_order__"] = features

print("Training IsolationForest on z-scored data (contamination=0.05)...")
model = IsolationForest(contamination=0.05, n_estimators=100,
                        random_state=42, n_jobs=-1)
model.fit(apply_zscore(df_tr, baselines, features))

pred = (model.predict(apply_zscore(df_te, baselines, features)) == -1).astype(int)
print(f"\nSMD serving model F1 (test): {f1_score(df_te['label'], pred):.3f}")

MODELS.mkdir(parents=True, exist_ok=True)
joblib.dump(model, MODELS / "smd_serving_model.pkl")
save_baselines(baselines, MODELS / "smd_serving_baselines.json")
print(f"\nSaved: {MODELS / 'smd_serving_model.pkl'}")
print(f"Saved: {MODELS / 'smd_serving_baselines.json'}")
print(f"Machines in artifact: {sum(1 for m in baselines if not m.startswith('__'))}")
