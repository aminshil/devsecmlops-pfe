"""
Rigorous evaluation: per-machine per-time-window baselines (C)
vs current per-machine baselines (A), with threshold tuning.
Full fleet sample + fair threshold comparison.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, roc_auc_score, precision_recall_curve

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "telecom_fleet.csv"
META_COLS = {"timestamp", "machine", "label", "type"}
MAX_ROWS = 400000
STD_FLOOR = 1e-8

print(f"Loading {DATA} (sampling {MAX_ROWS:,} rows across all machines)...")
df_full = pd.read_csv(DATA)
df = df_full.sample(n=min(MAX_ROWS, len(df_full)), random_state=42).reset_index(drop=True)
features = [c for c in df.columns if c not in META_COLS]
print(f"  {len(df):,} rows x {len(features)} features x {df['machine'].nunique()} machines")
print(f"  Anomaly ratio: {df['label'].mean()*100:.2f}%\n")

df["timestamp"] = pd.to_datetime(df["timestamp"])
df["hour"] = df["timestamp"].dt.hour
def assign_window(h):
    if h < 6:    return "night"
    elif h < 12: return "morning"
    elif h < 18: return "afternoon"
    else:        return "evening"
df["window"] = df["hour"].apply(assign_window)

df_tr, df_te = train_test_split(df, test_size=0.3, random_state=42, stratify=df["label"])
print(f"  Train: {len(df_tr):,}  Test: {len(df_te):,}\n")

def best_f1(y_true, y_score):
    prec, rec, thr = precision_recall_curve(y_true, y_score)
    f1s = 2 * prec * rec / (prec + rec + 1e-12)
    return f1s.max()

print("="*68)
print("A) Per-machine z-score (current shipped)")
print("="*68)
base_a = {}
for machine, grp in df_tr.groupby("machine"):
    base_a[machine] = {c: (float(grp[c].mean()),
                           float(max(grp[c].std(ddof=0), STD_FLOOR))) for c in features}
glob = {c: (float(df_tr[c].mean()), float(max(df_tr[c].std(ddof=0), 1.0))) for c in features}
type_a = {}
for mtype, grp in df_tr.groupby("type"):
    type_a[mtype] = {c: (float(grp[c].mean()),
                         float(max(grp[c].std(ddof=0), STD_FLOOR))) for c in features}

def z_a(split):
    out = split[features].copy().astype(float)
    for machine, idx in split.groupby("machine").groups.items():
        stats = base_a.get(machine, glob)
        for c in features:
            m, s = stats[c]
            out.loc[idx, c] = (split.loc[idx, c] - m) / s
    return out

Xtr, Xte = z_a(df_tr), z_a(df_te)
mdl = IsolationForest(contamination=0.068, n_estimators=200, random_state=42, n_jobs=-1)
mdl.fit(Xtr)
score_a = -mdl.score_samples(Xte)
pred_a = (mdl.predict(Xte) == -1).astype(int)
roc_a = roc_auc_score(df_te["label"], score_a)
f1_def_a = f1_score(df_te["label"], pred_a)
f1_best_a = best_f1(df_te["label"], score_a)
print(f"  F1 (default contam) : {f1_def_a:.4f}")
print(f"  F1 (best threshold) : {f1_best_a:.4f}")
print(f"  ROC-AUC             : {roc_a:.4f}\n")

print("="*68)
print("C) Per-machine per-time-window z-score")
print("="*68)
base_c = {}
for (machine, window), grp in df_tr.groupby(["machine", "window"]):
    base_c[f"{machine}|{window}"] = {c: (float(grp[c].mean()),
                                         float(max(grp[c].std(ddof=0), STD_FLOOR))) for c in features}

def z_c(split):
    out = split[features].copy().astype(float)
    for (machine, window), idx in split.groupby(["machine", "window"]).groups.items():
        key = f"{machine}|{window}"
        stats = base_c.get(key) or base_a.get(machine) or type_a.get(
                    split.loc[idx, "type"].iloc[0]) or glob
        for c in features:
            m, s = stats[c]
            out.loc[idx, c] = (split.loc[idx, c] - m) / s
    return out

Xtr_c, Xte_c = z_c(df_tr), z_c(df_te)
mdl_c = IsolationForest(contamination=0.068, n_estimators=200, random_state=42, n_jobs=-1)
mdl_c.fit(Xtr_c)
score_c = -mdl_c.score_samples(Xte_c)
pred_c = (mdl_c.predict(Xte_c) == -1).astype(int)
roc_c = roc_auc_score(df_te["label"], score_c)
f1_def_c = f1_score(df_te["label"], pred_c)
f1_best_c = best_f1(df_te["label"], score_c)
print(f"  F1 (default contam) : {f1_def_c:.4f}")
print(f"  F1 (best threshold) : {f1_best_c:.4f}")
print(f"  ROC-AUC             : {roc_c:.4f}")
print(f"  Window baselines    : {len(base_c):,}\n")

print("="*68)
print("VERDICT")
print("="*68)
print(f"{'Metric':<26}{'A (per-machine)':>18}{'C (per-window)':>18}")
print("-"*68)
print(f"{'F1 @ default contam':<26}{f1_def_a:>18.4f}{f1_def_c:>18.4f}")
print(f"{'F1 @ best threshold':<26}{f1_best_a:>18.4f}{f1_best_c:>18.4f}")
print(f"{'ROC-AUC':<26}{roc_a:>18.4f}{roc_c:>18.4f}")
print("-"*68)
if f1_best_c > f1_best_a and roc_c > roc_a:
    print("=> C wins on BOTH tuned-F1 and ROC-AUC. Worth switching.")
elif roc_c > roc_a and f1_best_c >= f1_best_a - 0.01:
    print("=> C better ranking, comparable tuned-F1. Switching justified.")
elif f1_def_a > f1_def_c:
    print("=> A wins on shipped metric. Keep A, document C as future work.")
else:
    print("=> Mixed. Decide by which metric matters most.")
