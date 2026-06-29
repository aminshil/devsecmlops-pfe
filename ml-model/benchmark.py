"""
Final benchmark with strict + point-adjusted F1.
Point-adjusted = OmniAnomaly/SMD standard: if ANY tick inside an anomaly block
is flagged, the WHOLE block counts as detected. Matches how production alerts work.
"""
import argparse, json, sys, time, warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from sklearn.neighbors import LocalOutlierFactor
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import (average_precision_score, f1_score, precision_score,
                              recall_score, roc_auc_score)
from sklearn.model_selection import train_test_split

from rich.console import Console
from rich.table import Table
from rich import box

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from preprocess import build_baselines, apply_zscore, save_baselines

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "models" / "results"
MODELS_DIR = ROOT / "models"
META_COLS = {"timestamp", "machine", "label", "type"}
console = Console()


def point_adjust(y_true, y_pred):
    """OmniAnomaly point-adjustment: if any tick in a true-anomaly contiguous block
    is flagged, mark every tick in that block as flagged. Returns adjusted y_pred."""
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred).copy()
    n = len(y_true); i = 0
    while i < n:
        if y_true[i] == 1:
            j = i
            while j < n and y_true[j] == 1: j += 1
            if y_pred[i:j].any(): y_pred[i:j] = 1
            i = j
        else: i += 1
    return y_pred


def adjusted_scores(y_true, scores):
    """For PR-AUC@adj: lift the score of every tick in a block to the block's max,
    so block-level detection is rewarded under threshold-independent metrics."""
    y_true = np.asarray(y_true); scores = np.asarray(scores).copy()
    n = len(y_true); i = 0
    while i < n:
        if y_true[i] == 1:
            j = i
            while j < n and y_true[j] == 1: j += 1
            scores[i:j] = scores[i:j].max()
            i = j
        else: i += 1
    return scores


def metrics(y_true, y_pred, scores):
    y_true_s = pd.Series(y_true).reset_index(drop=True)
    y_pred_s = pd.Series(y_pred).reset_index(drop=True)
    scores_s = pd.Series(scores).reset_index(drop=True)

    y_pred_adj = point_adjust(y_true_s.values, y_pred_s.values)
    scores_adj = adjusted_scores(y_true_s.values, scores_s.values)

    return {
        "precision": float(precision_score(y_true_s, y_pred_s, zero_division=0)),
        "recall": float(recall_score(y_true_s, y_pred_s, zero_division=0)),
        "f1": float(f1_score(y_true_s, y_pred_s, zero_division=0)),
        "f1_adj": float(f1_score(y_true_s, y_pred_adj, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true_s, scores_s)) if y_true_s.nunique() > 1 else None,
        "pr_auc": float(average_precision_score(y_true_s, scores_s)) if y_true_s.nunique() > 1 else None,
        "pr_auc_adj": float(average_precision_score(y_true_s, scores_adj)) if y_true_s.nunique() > 1 else None,
    }


def add_time_features(df, ts_col="timestamp"):
    ts = pd.to_datetime(df[ts_col]); hour, dow = ts.dt.hour, ts.dt.dayofweek
    return pd.DataFrame({
        "hour_sin": np.sin(2*np.pi*hour/24), "hour_cos": np.cos(2*np.pi*hour/24),
        "dow_sin": np.sin(2*np.pi*dow/7), "dow_cos": np.cos(2*np.pi*dow/7),
    }, index=df.index)


def render_table(rows, tag):
    f1s = [m["f1"] for m in rows.values()]
    best_f1, worst_f1 = max(f1s), min(f1s)
    table = Table(title=f"Model comparison — {tag}", box=box.ROUNDED, title_style="bold")
    table.add_column("model", style="white", no_wrap=True)
    for col in ("prec", "rec", "F1", "F1@adj", "ROC-AUC", "PR-AUC", "PR-AUC@adj"):
        table.add_column(col, justify="right")
    for name, mt in rows.items():
        style = ""
        if "IsolationForest (z-scored)" in name: style = "bold cyan"
        elif mt["f1"] == best_f1: style = "bold green"
        elif mt["f1"] == worst_f1: style = "red"
        auc = f"{mt['roc_auc']:.3f}" if mt['roc_auc'] is not None else "-"
        pr = f"{mt['pr_auc']:.3f}" if mt['pr_auc'] is not None else "-"
        pra = f"{mt['pr_auc_adj']:.3f}" if mt['pr_auc_adj'] is not None else "-"
        table.add_row(name, f"{mt['precision']:.3f}", f"{mt['recall']:.3f}",
                      f"{mt['f1']:.3f}", f"{mt['f1_adj']:.3f}", auc, pr, pra, style=style)
    console.print(table)
    console.print("[dim]bold cyan = shipped model · bold green = best strict F1 · red = worst strict F1[/dim]")
    console.print("[dim]@adj = point-adjusted (OmniAnomaly/SMD standard): any tick flagged in an anomaly block → whole block counted as detected[/dim]\n")


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--contamination", type=float, default=0.05)
    ap.add_argument("--n-estimators", type=int, default=100)
    ap.add_argument("--test-size", type=float, default=0.3)
    ap.add_argument("--max-rows", type=int, default=60000)
    ap.add_argument("--ocsvm-cap", type=int, default=5000)
    ap.add_argument("--lof-cap", type=int, default=20000)
    ap.add_argument("--time", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    df = pd.read_csv(args.data)
    if "machine" not in df.columns: df["machine"] = "single"
    features = [c for c in df.columns if c not in META_COLS]
    y_full = df["label"].astype(int)
    if args.max_rows and len(df) > args.max_rows:
        df, _ = train_test_split(df, train_size=args.max_rows, random_state=args.seed, stratify=y_full)
        df = df.reset_index(drop=True)
    y = df["label"].astype(int)
    tag = "WITH time features" if args.time else "no time features"
    console.print(f"[bold]{tag}[/bold]  ·  {len(df):,} rows × {len(features)} features  "
                  f"({df['machine'].nunique()} machines · anomaly {y.mean()*100:.2f}%)")

    idx_tr, idx_te = train_test_split(df.index, test_size=args.test_size, random_state=args.seed, stratify=y)
    df_tr, df_te = df.loc[idx_tr], df.loc[idx_te]
    y_tr, y_te = y.loc[idx_tr], y.loc[idx_te]
    baselines = build_baselines(df_tr, features)
    Xz_tr = apply_zscore(df_tr, baselines, features)
    Xz_te = apply_zscore(df_te, baselines, features)
    Xr_tr, Xr_te = df_tr[features].copy(), df_te[features].copy()

    if args.time and "timestamp" in df.columns:
        for Xset, dfset in [(Xz_tr, df_tr), (Xz_te, df_te), (Xr_tr, df_tr), (Xr_te, df_te)]:
            tf = add_time_features(dfset)
            for c in tf.columns: Xset[c] = tf[c].values
        console.print(f"  [dim]+4 time features -> {Xz_tr.shape[1]} cols[/dim]")

    rows = {}

    z_pred = (Xz_te[features].abs() > 3).any(axis=1).astype(int).values
    rows["z-threshold (|z|>3)"] = metrics(y_te, z_pred, Xz_te[features].abs().max(axis=1).values)

    m = IsolationForest(contamination=args.contamination, n_estimators=args.n_estimators, random_state=args.seed, n_jobs=-1).fit(Xr_tr)
    rows["IsolationForest (raw)"] = metrics(y_te, (m.predict(Xr_te) == -1).astype(int), -m.score_samples(Xr_te))

    m = IsolationForest(contamination=args.contamination, n_estimators=args.n_estimators, random_state=args.seed, n_jobs=-1).fit(Xz_tr)
    rows["IsolationForest (z-scored)"] = metrics(y_te, (m.predict(Xz_te) == -1).astype(int), -m.score_samples(Xz_te))

    fit = Xz_tr.sample(n=min(args.ocsvm_cap, len(Xz_tr)), random_state=args.seed)
    t = time.time()
    m = OneClassSVM(nu=min(args.contamination, 0.5), kernel="rbf", gamma="scale").fit(fit)
    rows["OneClassSVM (z-scored)"] = metrics(y_te, (m.predict(Xz_te) == -1).astype(int), -m.score_samples(Xz_te))
    console.print(f"  [dim]OCSVM on {len(fit)} rows, {time.time()-t:.1f}s[/dim]")

    fit = Xz_tr.sample(n=min(args.lof_cap, len(Xz_tr)), random_state=args.seed)
    m = LocalOutlierFactor(n_neighbors=20, novelty=True, contamination=args.contamination).fit(fit)
    rows["LocalOutlierFactor (z-scored)"] = metrics(y_te, (m.predict(Xz_te) == -1).astype(int), -m.score_samples(Xz_te))

    t = time.time()
    ae = MLPRegressor(hidden_layer_sizes=(16, 4, 16), activation="relu", solver="adam",
                      max_iter=300, early_stopping=True, n_iter_no_change=10, random_state=args.seed)
    ae.fit(Xz_tr, Xz_tr)
    err = ((Xz_te.values - ae.predict(Xz_te)) ** 2).mean(axis=1)
    thr = np.quantile(err, 1 - args.contamination)
    rows["Autoencoder (z-scored)"] = metrics(y_te, (err > thr).astype(int), err)
    console.print(f"  [dim]Autoencoder trained in {time.time()-t:.1f}s (slowest, CPU-bound)[/dim]\n")

    render_table(rows, tag)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S"); suffix = "_time" if args.time else ""
    out = RESULTS_DIR / f"benchmark_{ts}_{args.data.stem}{suffix}.json"
    with open(out, "w") as f:
        json.dump({"data": str(args.data), "time_features": args.time, "contamination": args.contamination,
                   "n_machines": int(df['machine'].nunique()), "anomaly_ratio": float(y.mean()), "models": rows}, f, indent=2)
    save_baselines(baselines, MODELS_DIR / f"baselines_{args.data.stem}.json")
    console.print(f"[dim]Saved: {out}[/dim]")


if __name__ == "__main__":
    run()
