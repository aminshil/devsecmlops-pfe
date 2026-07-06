"""
Head-to-head comparison: 3 baseline strategies
  A) Per-machine z-score, no time features        (current shipped)
  B) Per-machine z-score + explicit time features  (tested, lost)
  C) Per-machine PER-TIME-WINDOW z-score           (new idea)

Time windows: night=0-6, morning=6-12, afternoon=12-18, evening=18-24
"""
import sys, time
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "telecom_fleet.csv"
META_COLS = {"timestamp", "machine", "label", "type"}
MAX_ROWS = 80000
STD_FLOOR = 1e-8

print(f"Loading {DATA} (max {MAX_ROWS:,} rows) ...")
df = pd.read_csv(DATA, nrows=MAX_ROWS)
features = [c for c in df.columns if c not in META_COLS]
print(f"  {len(df):,} rows x {len(features)} features x {df['machine'].nunique()} machines")
print(f"  Anomaly ratio: {df['label'].mean()*100:.2f}%\n")

# Parse timestamps for time-window assignment
df["timestamp"] = pd.to_datetime(df["timestamp"])
df["hour"] = df["timestamp"].dt.hour

def assign_window(h):
    if h < 6:    return "night"
    elif h < 12: return "morning"
    elif h < 18: return "afternoon"
    else:        return "evening"

df["window"] = df["hour"].apply(assign_window)

# Add cyclical time features for approach B
df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
df["dow"]      = df["timestamp"].dt.dayofweek
df["dow_sin"]  = np.sin(2 * np.pi * df["dow"] / 7)
df["dow_cos"]  = np.cos(2 * np.pi * df["dow"] / 7)

time_features = ["hour_sin", "hour_cos", "dow_sin", "dow_cos"]

df_tr, df_te = train_test_split(df, test_size=0.3, random_state=42, stratify=df["label"])
print(f"  Train: {len(df_tr):,}  Test: {len(df_te):,}\n")

# ── Approach A: per-machine z-score, no time ────────────────────────────
print("=" * 70)
print("A) Per-machine z-score, NO time features (current shipped)")
print("=" * 70)
baselines_a = {}
for machine, grp in df_tr.groupby("machine"):
    baselines_a[machine] = {}
    for col in features:
        m = float(grp[col].mean())
        s = float(max(grp[col].std(ddof=0), STD_FLOOR))
        baselines_a[machine][col] = (m, s)
# global fallback
baselines_a["__global__"] = {
    col: (float(df_tr[col].mean()), float(max(df_tr[col].std(ddof=0), 1.0)))
    for col in features
}

def zscore_a(split):
    out = split[features].copy().astype(float)
    for machine, idx in split.groupby("machine").groups.items():
        stats = baselines_a.get(machine, baselines_a["__global__"])
        for col in features:
            m, s = stats[col]
            out.loc[idx, col] = (split.loc[idx, col] - m) / s
    return out

X_tr_a = zscore_a(df_tr)
X_te_a = zscore_a(df_te)
t0 = time.time()
model_a = IsolationForest(contamination=0.068, n_estimators=200, random_state=42, n_jobs=-1)
model_a.fit(X_tr_a)
t_a = time.time() - t0
y_pred_a = (model_a.predict(X_te_a) == -1).astype(int)
y_score_a = -model_a.score_samples(X_te_a)
f1_a   = f1_score(df_te["label"], y_pred_a)
prec_a = precision_score(df_te["label"], y_pred_a)
rec_a  = recall_score(df_te["label"], y_pred_a)
roc_a  = roc_auc_score(df_te["label"], y_score_a)
print(f"  F1={f1_a:.4f}  Prec={prec_a:.4f}  Rec={rec_a:.4f}  ROC-AUC={roc_a:.4f}  ({t_a:.1f}s)\n")

# ── Approach B: per-machine z-score + time features ──────────────────────
print("=" * 70)
print("B) Per-machine z-score + 4 explicit time features")
print("=" * 70)
features_b = features + time_features

def zscore_b(split):
    out = split[features].copy().astype(float)
    for machine, idx in split.groupby("machine").groups.items():
        stats = baselines_a.get(machine, baselines_a["__global__"])
        for col in features:
            m, s = stats[col]
            out.loc[idx, col] = (split.loc[idx, col] - m) / s
    for tf in time_features:
        out[tf] = split[tf].values
    return out

X_tr_b = zscore_b(df_tr)
X_te_b = zscore_b(df_te)
t0 = time.time()
model_b = IsolationForest(contamination=0.068, n_estimators=200, random_state=42, n_jobs=-1)
model_b.fit(X_tr_b)
t_b = time.time() - t0
y_pred_b = (model_b.predict(X_te_b) == -1).astype(int)
y_score_b = -model_b.score_samples(X_te_b)
f1_b   = f1_score(df_te["label"], y_pred_b)
prec_b = precision_score(df_te["label"], y_pred_b)
rec_b  = recall_score(df_te["label"], y_pred_b)
roc_b  = roc_auc_score(df_te["label"], y_score_b)
print(f"  F1={f1_b:.4f}  Prec={prec_b:.4f}  Rec={rec_b:.4f}  ROC-AUC={roc_b:.4f}  ({t_b:.1f}s)\n")

# ── Approach C: per-machine PER-TIME-WINDOW z-score ──────────────────────
print("=" * 70)
print("C) Per-machine PER-TIME-WINDOW z-score (new approach)")
print("   Windows: night=0-6  morning=6-12  afternoon=12-18  evening=18-24")
print("=" * 70)
baselines_c = {}
for (machine, window), grp in df_tr.groupby(["machine", "window"]):
    key = f"{machine}__{window}"
    baselines_c[key] = {}
    for col in features:
        m = float(grp[col].mean())
        s = float(max(grp[col].std(ddof=0), STD_FLOOR))
        baselines_c[key][col] = (m, s)

# Fallback: per-machine (across all windows)
for machine, grp in df_tr.groupby("machine"):
    key = f"{machine}__fallback"
    baselines_c[key] = {}
    for col in features:
        m = float(grp[col].mean())
        s = float(max(grp[col].std(ddof=0), STD_FLOOR))
        baselines_c[key][col] = (m, s)

baselines_c["__global__"] = baselines_a["__global__"]

def zscore_c(split):
    out = split[features].copy().astype(float)
    for (machine, window), idx in split.groupby(["machine", "window"]).groups.items():
        key = f"{machine}__{window}"
        if key not in baselines_c:
            key = f"{machine}__fallback"
        if key not in baselines_c:
            key = "__global__"
        stats = baselines_c[key]
        for col in features:
            m, s = stats[col]
            out.loc[idx, col] = (split.loc[idx, col] - m) / s
    return out

X_tr_c = zscore_c(df_tr)
X_te_c = zscore_c(df_te)
t0 = time.time()
model_c = IsolationForest(contamination=0.068, n_estimators=200, random_state=42, n_jobs=-1)
model_c.fit(X_tr_c)
t_c = time.time() - t0
y_pred_c = (model_c.predict(X_te_c) == -1).astype(int)
y_score_c = -model_c.score_samples(X_te_c)
f1_c   = f1_score(df_te["label"], y_pred_c)
prec_c = precision_score(df_te["label"], y_pred_c)
rec_c  = recall_score(df_te["label"], y_pred_c)
roc_c  = roc_auc_score(df_te["label"], y_score_c)
print(f"  F1={f1_c:.4f}  Prec={prec_c:.4f}  Rec={rec_c:.4f}  ROC-AUC={roc_c:.4f}  ({t_c:.1f}s)\n")

# ── Summary ──────────────────────────────────────────────────────────────
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"{'Approach':<50} {'F1':>7} {'Prec':>7} {'Rec':>7} {'ROC':>7}")
print("-" * 70)
results = [
    ("A) per-machine z-score (current shipped)", f1_a, prec_a, rec_a, roc_a),
    ("B) per-machine z-score + time features", f1_b, prec_b, rec_b, roc_b),
    ("C) per-machine per-time-window z-score (NEW)", f1_c, prec_c, rec_c, roc_c),
]
best_f1 = max(r[1] for r in results)
for name, f1, prec, rec, roc in results:
    marker = " <<<< BEST" if f1 == best_f1 else ""
    print(f"{name:<50} {f1:>7.4f} {prec:>7.4f} {rec:>7.4f} {roc:>7.4f}{marker}")

print(f"\nBaselines built: {len(baselines_c):,} time-window entries")
print("If C wins, it solves the 3am problem without adding dimensions.")
