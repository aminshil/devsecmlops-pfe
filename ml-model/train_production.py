#!/usr/bin/env python3
"""
Production model pipeline: train -> evaluate -> guardrail -> (promote).

Reproduces the serving models from the versioned datasets, and is the ONLY
path by which a model reaches production:

  1. Fingerprint the inputs (SHA-256 + row counts of every dataset file).
  2. Train a candidate:
       baselines  per machine x time window (+ machine / type / global
                  fallbacks) fitted on ALL training rows
       v3         XGBoost cause classifier on the 6 z-scored features
       v4         XGBoost on 6 z-scored + 9 rolling features (window of 10)
       safety net IsolationForest on the 6 z-scored features
     Training set for the classifiers: every anomaly row + a seeded sample of
     normal rows (--normal-sample); cascade rows are labelled "normal"
     because the generator labels cascades inconsistently (README, Engineering
     Decision 9) -- the IsolationForest covers them instead.
     Optional operator feedback (--feedback, produced by
     scripts/export_feedback_dataset.py) is appended with features recomputed
     from the stored raw metrics and history using the CANDIDATE's baselines.
  3. Evaluate the candidate AND the current production artifacts with the
     same code, on the same independent test set, using the serving decision
     rule (ml-model/decision.py) at the production threshold.
  4. Guardrail: the candidate is promotable only if, for both the v4 (primary)
     and v3 (fallback) serving paths, F1 does not drop by more than
     --max-f1-drop and no cause's recall drops by more than --max-recall-drop.
  5. --promote (only if the guardrail passes): write the artifacts to models/,
     record the full lineage (datasets, parameters, metrics, artifact hashes,
     git commit, MLflow run) in models/manifest.json. The committed artifacts
     are what CI bakes into the image; CI refuses to build if their hashes do
     not match the manifest (scripts/verify_model_manifest.py).

Every run (promoted or not) is appended to the manifest history and, when
MLFLOW_TRACKING_URI is set, logged to MLflow.

Usage (on the VM, from the repo root):
  python ml-model/train_production.py                 # evaluate only
  python ml-model/train_production.py --promote       # promote if it passes
  python ml-model/train_production.py --feedback data/feedback/feedback_*.csv --promote
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))
import decision  # noqa: E402
from preprocess import (add_rolling_features, add_window_column,  # noqa: E402
                        apply_zscore, build_baselines,
                        rolling_feature_names, rolling_features_from_history,
                        ROLLING_FEATURE_BASE_COLS, ROLLING_WINDOW_SIZE)

FEATURES = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]
ROLLING = rolling_feature_names()
MODELS = ROOT / "models"
MANIFEST_FILENAME = "manifest.json"
MANIFEST = MODELS / MANIFEST_FILENAME

# Serving artifact file names (what api/app.py loads for MODEL_NAME=telecom_v3)
ARTIFACTS = {
    "v3_model":   "telecom_xgb_classifier_v2.pkl",
    "v3_encoder": "telecom_xgb_label_encoder_v2.pkl",
    "v4_model":   "telecom_xgb_v4_rolling.pkl",
    "v4_encoder": "telecom_xgb_v4_rolling_encoder.pkl",
    "iso_model":  "telecom_iso_v2.pkl",
    "baselines":  "telecom_baselines_v2.json",
}

# Hyperparameters of the models currently in production (read back from the
# committed pickles: XGBClassifier.get_params(), IsolationForest.get_params()).
# NOSONAR (python:S6711): random_state=42 IS set on both, passed via **dict
# unpacking at the constructor call sites below -- static analysis cannot
# trace a keyword through dict unpacking back to its literal definition here,
# so it reports these constructors as unseeded. Verified directly: both
# XGBClassifier(**XGB_PARAMS, ...) calls and IsolationForest(**ISO_PARAMS, ...)
# genuinely receive random_state=42 at runtime.
XGB_PARAMS = dict(objective="multi:softprob", n_estimators=150, max_depth=6,
                  learning_rate=0.1, random_state=42, n_jobs=-1, verbosity=0)
ISO_PARAMS = dict(n_estimators=200, random_state=42, n_jobs=-1)
SEED = 42
USECOLS = ["timestamp", "machine", "type", *FEATURES, "label", "anomaly_type"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return None


def load_fleet(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=USECOLS, dtype={"anomaly_type": "string"})
    for c in FEATURES:
        df[c] = df[c].astype("float32")
    df = add_window_column(df)
    df["cause"] = np.where(df["label"] == 1, df["anomaly_type"].fillna("normal"), "normal")
    df.loc[df["cause"] == "cascade", "cause"] = "normal"   # Engineering Decision 9
    return df


def fleet_features(df: pd.DataFrame, baselines: dict) -> tuple[np.ndarray, np.ndarray]:
    """(X6, X15) for a time-series fleet frame; rolling features on raw values,
    per machine in time order -- exactly as at serving time."""
    df = add_rolling_features(df)                     # sorts by machine, timestamp
    z = apply_zscore(df, baselines, FEATURES).to_numpy(dtype=np.float32)
    roll = df[ROLLING].to_numpy(dtype=np.float32)
    return df, z, np.hstack([z, roll])


# ---------------------------------------------------------------------------
# Feedback rows (exported from PostgreSQL by scripts/export_feedback_dataset.py)
# ---------------------------------------------------------------------------
def feedback_examples(paths: list[Path], baselines: dict, known_causes: set[str]):
    """Operator verdicts -> labelled examples, features recomputed from the
    stored raw metrics (and history for v4) with the candidate's baselines.

      false_positive / true_negative  -> "normal"
      true_positive with a classifier cause the model knows -> that cause
      true_positive flagged only by the safety net, false_negative
                                      -> cause unknown: not usable as a
                                         classifier label, counted and skipped
    """
    rows = []
    for p in paths:
        rows.append(pd.read_csv(p))
    if not rows:
        return None
    fb = pd.concat(rows, ignore_index=True).drop_duplicates(subset="id", keep="last")
    label = []
    for v, cause in zip(fb["operator_verdict"], fb["final_cause"].fillna("")):
        if v in ("false_positive", "true_negative"):
            label.append("normal")
        elif v == "true_positive" and cause in known_causes:
            label.append(cause)
        else:
            label.append(None)
    fb["cause"] = label
    skipped = int(fb["cause"].isna().sum())
    fb = fb[fb["cause"].notna()].reset_index(drop=True)
    raw = pd.json_normalize(fb["raw_metrics_json"].map(json.loads))
    frame = pd.DataFrame({"machine": fb["machine"], "type": fb["machine_type"],
                          "window": fb["time_window"]})
    for c in FEATURES:
        frame[c] = raw[c].astype("float32")
    x6 = apply_zscore(frame, baselines, FEATURES).to_numpy(dtype=np.float32)
    has_hist = fb["history_json"].notna() if "history_json" in fb else pd.Series(False, index=fb.index)
    roll = np.array([rolling_features_from_history(json.loads(h)) if ok else [np.nan] * len(ROLLING)
                     for h, ok in zip(fb.get("history_json", [None] * len(fb)), has_hist)],
                    dtype=np.float32)
    return {"x6": x6, "roll": roll, "has_history": has_hist.to_numpy(),
            "cause": fb["cause"].to_numpy(), "used": len(fb), "skipped_unknown_cause": skipped}


# ---------------------------------------------------------------------------
# Evaluation with the serving decision rule
# ---------------------------------------------------------------------------
def evaluate(clf, enc, iso, x_clf, x_iso, y_bin, y_type, threshold) -> dict:
    proba = clf.predict_proba(x_clf)
    hit, cause = decision.classifier_vote_batch(proba, list(enc.classes_), threshold)
    iso_hit = iso.predict(x_iso) == -1
    pred, final_cause = decision.final_verdict_batch(hit, cause, iso_hit)
    out = {
        "f1": float(f1_score(y_bin, pred, zero_division=0)),
        "precision": float(precision_score(y_bin, pred, zero_division=0)),
        "recall": float(recall_score(y_bin, pred, zero_division=0)),
        "classifier_only_f1": float(f1_score(y_bin, hit, zero_division=0)),
        "per_cause_recall": {},
    }
    for t in sorted(set(y_type[y_bin == 1])):
        m = y_type == t
        out["per_cause_recall"][t] = float(pred[m].mean())
    named = (y_bin == 1) & (y_type != "cascade") & hit
    out["cause_accuracy"] = float((final_cause[named] == y_type[named]).mean()) if named.any() else None
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()} | {
        "per_cause_recall": {k: round(v, 4) for k, v in out["per_cause_recall"].items()}}


def guardrail(cand: dict, prod: dict, max_f1_drop: float, max_recall_drop: float) -> tuple[bool, list[str]]:
    reasons = []
    for path in ("v4", "v3"):
        c, p = cand[path], prod[path]
        if c["f1"] < p["f1"] - max_f1_drop:
            reasons.append(f"{path} F1 {p['f1']:.4f} -> {c['f1']:.4f}")
        for cause, pr in p["per_cause_recall"].items():
            cr = c["per_cause_recall"].get(cause, 0.0)
            if cr < pr - max_recall_drop:
                reasons.append(f"{path} {cause} recall {pr:.4f} -> {cr:.4f}")
    return (not reasons), reasons


def evaluate_set(art_dir: Path, test: pd.DataFrame, threshold: float) -> dict:
    b = json.load(open(art_dir / ARTIFACTS["baselines"]))
    t, z, z15 = fleet_features(test.copy(), b)
    yb, yt = t["label"].to_numpy(), t["anomaly_type"].fillna("normal").to_numpy()
    m = {k: joblib.load(art_dir / v) for k, v in ARTIFACTS.items() if v.endswith(".pkl")}
    return {
        "v3": evaluate(m["v3_model"], m["v3_encoder"], m["iso_model"], z, z, yb, yt, threshold),
        "v4": evaluate(m["v4_model"], m["v4_encoder"], m["iso_model"], z15, z, yb, yt, threshold),
        "v4_at_0.5": evaluate(m["v4_model"], m["v4_encoder"], m["iso_model"], z15, z, yb, yt, 0.5),
    }


def rel(p: Path) -> str:
    p = p.resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def fingerprint(paths: dict[str, Path]) -> dict:
    out = {}
    for k, p in paths.items():
        if not p.exists():
            raise FileNotFoundError(p)
        out[k] = {"file": rel(p), "sha256": sha256(p)}
    return out


def read_manifest(models_dir: Path) -> dict:
    path = models_dir / MANIFEST_FILENAME
    return json.load(open(path)) if path.exists() else {}


def write_manifest(models_dir: Path, manifest: dict) -> None:
    with open(models_dir / MANIFEST_FILENAME, "w") as f:
        json.dump(manifest, f, indent=2)


def evaluate_production_only(args) -> int:
    """Record what the committed production artifacts score on the
    independent test set, with the serving decision rule."""
    test_fp = fingerprint({"test": args.test})["test"]
    test = load_fleet(args.test)
    test_fp["rows"] = int(len(test))
    ev = evaluate_set(args.models_dir, test, args.threshold)
    for p in ("v3", "v4", "v4_at_0.5"):
        log(f"production {p}: F1={ev[p]['f1']:.4f} P={ev[p]['precision']:.4f} "
            f"R={ev[p]['recall']:.4f} classifier-only F1={ev[p]['classifier_only_f1']:.4f}")
    manifest = read_manifest(args.models_dir)
    manifest["schema"] = 2
    prod = manifest.setdefault("production", {})
    prod.update({
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation": {"test_set": test_fp, "threshold": args.threshold, **ev},
        "artifacts": {f"models/{n}": sha256(args.models_dir / n) for n in ARTIFACTS.values()},
    })
    write_manifest(args.models_dir, manifest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(prod, indent=2))
    log("manifest updated with the production evaluation")
    return 0


# ---------------------------------------------------------------------------
# Training steps
# ---------------------------------------------------------------------------
def training_matrices(train: pd.DataFrame, baselines: dict, normal_sample: int):
    """Full-data z-scores (IsolationForest) + the classifier training subset:
    every anomaly row and a seeded sample of normal rows."""
    train, x6_all, x15_all = fleet_features(train, baselines)
    rng = np.random.default_rng(SEED)
    labels = train["label"].to_numpy()
    anom_idx = np.flatnonzero(labels == 1)
    norm_idx = np.flatnonzero(labels == 0)
    n_norm = min(normal_sample, len(norm_idx))
    sel = np.sort(np.concatenate([anom_idx, rng.choice(norm_idx, n_norm, replace=False)]))
    log(f"classifier training rows: {len(sel):,} ({len(anom_idx):,} anomalies + {n_norm:,} normal)")
    return x6_all, x6_all[sel], x15_all[sel], train["cause"].to_numpy()[sel], n_norm


def add_feedback(paths, baselines, x6, x15, y):
    """Append usable feedback rows; v4 only receives rows with stored history."""
    if not paths:
        return x6, y, x15, y, None
    fb = feedback_examples(paths, baselines, set(np.unique(y)))
    if not fb or not fb["used"]:
        return x6, y, x15, y, None
    hh = fb["has_history"]
    info = {"rows_used_v3": fb["used"], "rows_used_v4": int(hh.sum()),
            "rows_skipped_unknown_cause": fb["skipped_unknown_cause"]}
    log(f"feedback: {info}")
    return (np.vstack([x6, fb["x6"]]), np.concatenate([y, fb["cause"]]),
            np.vstack([x15, np.hstack([fb["x6"][hh], fb["roll"][hh]])]),
            np.concatenate([y, fb["cause"][hh]]), info)


def train_candidate(x6, y6, x15, y15, x_iso, contamination, baselines, out_dir: Path) -> None:
    enc3, enc4 = LabelEncoder().fit(y6), LabelEncoder().fit(y15)
    t0 = time.time()
    v3 = XGBClassifier(**XGB_PARAMS, eval_metric="mlogloss").fit(x6, enc3.transform(y6))
    v4 = XGBClassifier(**XGB_PARAMS).fit(x15, enc4.transform(y15))
    iso = IsolationForest(**ISO_PARAMS, contamination=contamination).fit(x_iso)
    log(f"candidate trained in {time.time() - t0:.0f}s (iso contamination={contamination:.5f})")
    out_dir.mkdir(parents=True, exist_ok=True)
    for key, obj in (("v3_model", v3), ("v3_encoder", enc3), ("v4_model", v4),
                     ("v4_encoder", enc4), ("iso_model", iso)):
        joblib.dump(obj, out_dir / ARTIFACTS[key])
    with open(out_dir / ARTIFACTS["baselines"], "w") as f:
        json.dump(baselines, f)


def log_to_mlflow(run: dict, work_dir: Path) -> str | None:
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        return None
    try:
        import mlflow
        mlflow.set_experiment("telecom-production-model")
        with mlflow.start_run() as r:
            p = run["params"]
            mlflow.log_params({"normal_sample": p["normal_sample"], "threshold": p["threshold"],
                               "xgb_n_estimators": p["xgb"]["n_estimators"],
                               "xgb_max_depth": p["xgb"]["max_depth"],
                               "iso_contamination": round(p["iso"]["contamination"], 6)})
            for path in ("v3", "v4"):
                for k in ("f1", "precision", "recall"):
                    mlflow.log_metric(f"{path}_{k}", run["candidate_evaluation"][path][k])
            mlflow.set_tags({"git_commit": run["git_commit"] or "",
                             "guardrail": str(run["guardrail_passed"]),
                             "train_sha256": run["datasets"]["train"]["sha256"],
                             "test_sha256": run["datasets"]["test"]["sha256"]})
            mlflow.log_artifacts(str(work_dir), artifact_path="model")
            return r.info.run_id
    except Exception as e:
        log(f"MLflow logging skipped: {e}")
        return None


def record_run(args, run: dict) -> None:
    """Append the run to the manifest history; on --promote with a passing
    guardrail, install the artifacts and make them the production entry."""
    manifest = read_manifest(args.models_dir)
    manifest.setdefault("schema", 2)
    manifest.setdefault("history", [])
    if args.promote and run["guardrail_passed"]:
        for name in ARTIFACTS.values():
            shutil.copy2(args.work_dir / name, args.models_dir / name)
        run["promoted"] = True
        manifest["production"] = {
            "promoted_at": run["finished_at"],
            "source": "ml-model/train_production.py",
            "git_commit_at_training": run["git_commit"],
            "mlflow_run_id": run["mlflow_run_id"],
            "datasets": run["datasets"],
            "feedback": run["feedback"],
            "params": run["params"],
            "evaluation": {"test_set": run["datasets"]["test"], "threshold": args.threshold,
                           **run["candidate_evaluation"]},
            "artifacts": {f"models/{n}": sha256(args.models_dir / n) for n in ARTIFACTS.values()},
        }
        log("PROMOTED: artifacts written, manifest updated")
    elif args.promote:
        log("not promoted: guardrail rejected the candidate")
    manifest["history"].append({k: run[k] for k in (
        "finished_at", "git_commit", "datasets", "feedback", "guardrail_passed",
        "guardrail_reasons", "promoted", "mlflow_run_id")} | {
        "candidate_f1": {p: run["candidate_evaluation"][p]["f1"] for p in ("v3", "v4")}})
    write_manifest(args.models_dir, manifest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(run, indent=2))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", type=Path, default=ROOT / "data/telecom_fleet_v2_labeled.csv")
    ap.add_argument("--test", type=Path, default=ROOT / "data/telecom_fleet_v2_test.csv")
    ap.add_argument("--feedback", type=Path, nargs="*", default=[])
    ap.add_argument("--normal-sample", type=int, default=2_000_000)
    ap.add_argument("--threshold", type=float, default=None,
                    help="decision threshold; default: production.decision_threshold from the "
                         "manifest (chosen by ml-model/select_threshold.py), else "
                         "$PREDICT_THRESHOLD, else 0.85")
    ap.add_argument("--max-f1-drop", type=float, default=0.0)
    ap.add_argument("--max-recall-drop", type=float, default=0.02)
    ap.add_argument("--models-dir", type=Path, default=MODELS)
    ap.add_argument("--work-dir", type=Path, default=ROOT / "build" / "candidate")
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--report", type=Path, default=None, help="write the run report JSON here")
    ap.add_argument("--evaluate-production", action="store_true",
                    help="no training: evaluate the committed production artifacts on the "
                         "test set and record the result + artifact hashes in the manifest")
    args = ap.parse_args(argv)
    if args.threshold is None:
        selected = (read_manifest(args.models_dir).get("production") or {}).get("decision_threshold")
        args.threshold = float(selected if selected is not None
                               else os.environ.get("PREDICT_THRESHOLD", "0.85"))
    return args


def run_pipeline(args) -> int:
    started = datetime.now(timezone.utc)
    inputs = {"train": args.train, "test": args.test}
    inputs.update({f"feedback_{i}": p for i, p in enumerate(args.feedback)})
    datasets = fingerprint(inputs)
    log("dataset fingerprints: " + ", ".join(f"{k}={v['sha256'][:12]}" for k, v in datasets.items()))

    train = load_fleet(args.train)
    datasets["train"]["rows"] = int(len(train))
    contamination = float(train["label"].mean())
    baselines = build_baselines(train, FEATURES)
    x_iso, x6, x15, y, n_norm = training_matrices(train, baselines, args.normal_sample)
    del train
    x6, y6, x15, y15, fb_info = add_feedback(args.feedback, baselines, x6, x15, y)
    train_candidate(x6, y6, x15, y15, x_iso, contamination, baselines, args.work_dir)
    del x_iso

    test = load_fleet(args.test)
    datasets["test"]["rows"] = int(len(test))
    cand_eval = evaluate_set(args.work_dir, test, args.threshold)
    log(f"candidate  v4 F1={cand_eval['v4']['f1']:.4f}  v3 F1={cand_eval['v3']['f1']:.4f}")
    has_prod = all((args.models_dir / v).exists() for v in ARTIFACTS.values())
    prod_eval = evaluate_set(args.models_dir, test, args.threshold) if has_prod else None
    if prod_eval:
        log(f"production v4 F1={prod_eval['v4']['f1']:.4f}  v3 F1={prod_eval['v3']['f1']:.4f}")
        passed, reasons = guardrail(cand_eval, prod_eval, args.max_f1_drop, args.max_recall_drop)
    else:
        passed, reasons = True, ["no production artifacts present -- initial training"]
    log("GUARDRAIL " + ("PASS" if passed else "REJECT: " + "; ".join(reasons)))

    run = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "datasets": datasets,
        "feedback": fb_info,
        "params": {"xgb": XGB_PARAMS, "iso": {**ISO_PARAMS, "contamination": contamination},
                   "normal_sample": n_norm, "rolling_window": ROLLING_WINDOW_SIZE,
                   "rolling_base_cols": list(ROLLING_FEATURE_BASE_COLS), "threshold": args.threshold,
                   "max_f1_drop": args.max_f1_drop, "max_recall_drop": args.max_recall_drop},
        "candidate_evaluation": cand_eval,
        "production_evaluation": prod_eval,
        "guardrail_passed": passed,
        "guardrail_reasons": reasons,
        "promoted": False,
        "mlflow_run_id": None,
    }
    run["mlflow_run_id"] = log_to_mlflow(run, args.work_dir)
    record_run(args, run)
    # exit code drives CI: 0 = passed (and promoted if requested), 1 = rejected
    return 0 if passed else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.evaluate_production:
            return evaluate_production_only(args)
        return run_pipeline(args)
    except FileNotFoundError as e:
        log(f"FATAL: missing dataset {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
