"""
Variant C: SMD with top-3 highest-variance features per machine, windowed baselines.
NOTE: "cpu/ram/network" names are per-machine variance rankings, not a fixed
semantic feature across machines (each machine's top-3 columns differ).
"""
import sys
from pathlib import Path
import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import build_baselines, apply_zscore, save_baselines, add_window_column

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "smd_top3.csv"
MODELS = ROOT / "models"
META_COLS = {"timestamp", "machine", "label", "window"}

print(f"Loading {DATA} ...")
df = pd.read_csv(DATA)
df = add_window_column(df)
features = [c for c in df.columns if c not in META_COLS]
print(f"  {len(df):,} rows  x  {len(features)} features  x  {df['machine'].nunique()} machines")
print(f"  Anomaly ratio: {df['label'].mean()*100:.2f}%")

df_tr, df_te = train_test_split(df, test_size=0.3, random_state=42, stratify=df["label"])
print(f"  Train: {len(df_tr):,}  Test: {len(df_te):,}\n")

baselines = build_baselines(df_tr, features)
baselines["__feature_order__"] = features

model = IsolationForest(contamination=0.05, n_estimators=100, random_state=42, n_jobs=-1)
model.fit(apply_zscore(df_tr, baselines, features))

pred = (model.predict(apply_zscore(df_te, baselines, features)) == -1).astype(int)
f1 = f1_score(df_te["label"], pred)
prec = precision_score(df_te["label"], pred)
rec = recall_score(df_te["label"], pred)
print(f"\nVariant C (top3, windowed) SMD model:")
print(f"  F1={f1:.3f}  Precision={prec:.3f}  Recall={rec:.3f}")

MODELS.mkdir(parents=True, exist_ok=True)
joblib.dump(model, MODELS / "smd_serving_model_top3.pkl")
save_baselines(baselines, MODELS / "smd_serving_baselines_top3.json")
print(f"\nSaved: smd_serving_model_top3.pkl")
