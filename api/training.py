"""
Training console backend for the control panel (MLOps tab).

This is a DEMO/OPS tool, not part of the production serving path. It lets the
operator pick a dataset, a model, and hyperparameters, train for real on a
capped sample (so it is fast and cannot OOM the VM), compare runs, verify a
new model against the production baseline with the SAME guardrail the retrain
pipeline uses, and — only if the guardrail passes — register the artifact and
surface the exact kubectl deploy commands (shown, never executed here).

Real training: reuses ml-model/preprocess.py (build_baselines, apply_zscore,
add_window_column) so the z-scoring is identical to production. Nothing here
is faked.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (f1_score, precision_score, recall_score,
                             roc_auc_score)
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"

# Production baseline to verify against (the shipped v4 offline numbers).
# Sourced from the README/model card; used only as the guardrail reference.
PRODUCTION_BASELINE = {
    "model": "v4 XGBoost + IsolationForest (production)",
    "f1": 0.816,
    "precision": 0.931,
    "recall": 0.726,
}
GUARDRAIL_MIN_F1_RATIO = 1.0  # new F1 must be >= production F1

META_COLS = {"timestamp", "machine", "label", "type", "hour", "window",
             "anomaly_type"}

# In-memory run log for this session (newest first). Not persisted — it is a
# live experiment console, and MLflow is the durable tracker.
_RUNS: list[dict] = []
_RUNS_LOCK = threading.Lock()

# Only one training job at a time (OOM protection on the shared VM).
_TRAIN_LOCK = threading.Lock()
_ACTIVE = {"running": False, "status": "", "run_id": None}

# preprocess.py lives in ml-model/ — import it lazily so a bad import here
# never breaks the serving app that mounts these routes.
import sys
sys.path.insert(0, str(ROOT / "ml-model"))


def list_datasets() -> list[dict]:
    """Return the real CSVs under data/ with size (rows are counted lazily)."""
    out = []
    for p in sorted(DATA_DIR.glob("*.csv")):
        size_mb = p.stat().st_size / (1024 * 1024)
        out.append({
            "name": p.name,
            "size_mb": round(size_mb, 1),
            "big": size_mb > 50,  # flag files where a full read is heavy
        })
    return out


def _load_sampled(dataset: str, sample_rows: int):
    """
    Load up to sample_rows from the dataset. For big files we read the head
    (fast, bounded memory); sample_rows<=0 means the full file.
    Returns (df, note).
    """
    path = DATA_DIR / dataset
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {dataset}")

    if sample_rows and sample_rows > 0:
        df = pd.read_csv(path, nrows=sample_rows)
        note = f"sampled first {len(df):,} rows"
    else:
        df = pd.read_csv(path)
        note = f"full dataset ({len(df):,} rows)"
    return df, note


def _prep_features(df):
    """Add window column (if timestamps exist) and return the numeric feature
    list. Schema-adaptive: works for fleet files (machine + 6 features +
    timestamp) and for SMD-style files (a few features, no machine/timestamp)."""
    if "timestamp" in df.columns:
        try:
            from preprocess import add_window_column  # noqa: E402
            df = add_window_column(df)
        except Exception:
            pass  # no timestamps parseable -> skip windowing, still trainable
    features = [c for c in df.columns if c not in META_COLS
                and pd.api.types.is_numeric_dtype(df[c])]
    return df, features


def _train_isoforest(df, features, params):
    """Real IsolationForest training on z-scored features (reuses preprocess)."""
    from preprocess import build_baselines, apply_zscore  # noqa: E402

    if "label" not in df.columns:
        raise ValueError("dataset has no 'label' column to evaluate against")

    df_tr, df_te = train_test_split(
        df, test_size=0.3, random_state=42,
        stratify=df["label"] if df["label"].nunique() > 1 else None)

    # Schema-adaptive: build_baselines needs a 'machine' column. If the
    # dataset has none (e.g. SMD), synthesize a single global machine so the
    # same per-machine z-scoring code path still works honestly.
    if "machine" not in df_tr.columns:
        df_tr = df_tr.copy(); df_te = df_te.copy()
        df_tr["machine"] = "__all__"; df_te["machine"] = "__all__"
    baselines = build_baselines(df_tr, features)
    X_tr = apply_zscore(df_tr, baselines, features)
    X_te = apply_zscore(df_te, baselines, features)

    model = IsolationForest(
        contamination=float(params.get("contamination", 0.068)),
        n_estimators=int(params.get("n_estimators", 200)),
        random_state=42, n_jobs=-1)
    model.fit(X_tr)

    y_pred = (model.predict(X_te) == -1).astype(int)
    y_score = -model.score_samples(X_te)
    y_true = df_te["label"].astype(int)

    metrics = {
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
    }
    try:
        metrics["roc_auc"] = round(float(roc_auc_score(y_true, y_score)), 4)
    except Exception:
        metrics["roc_auc"] = None
    return model, baselines, metrics, len(df_tr), len(df_te)


def _run_training(run_id, dataset, model_name, params, sample_rows):
    """Background worker: does the real training and records the run."""
    try:
        _ACTIVE["status"] = "loading dataset…"
        df, data_note = _load_sampled(dataset, sample_rows)

        _ACTIVE["status"] = "preprocessing…"
        df, features = _prep_features(df)
        if not features:
            raise ValueError("no numeric feature columns found")

        _ACTIVE["status"] = f"training {model_name}…"
        t0 = time.time()
        if model_name == "isolation_forest":
            model, baselines, metrics, n_tr, n_te = _train_isoforest(
                df, features, params)
        else:
            raise ValueError(f"model '{model_name}' not supported in the live "
                             f"trainer yet (use isolation_forest)")
        train_secs = round(time.time() - t0, 1)

        # save the trained artifact to a run-scoped file (gitignored *.pkl)
        art = MODELS_DIR / f"run_{run_id}.pkl"
        joblib.dump({"model": model, "baselines": baselines,
                     "features": features}, art)

        run = {
            "run_id": run_id,
            "dataset": dataset,
            "data_note": data_note,
            "model": model_name,
            "params": {"contamination": float(params.get("contamination", 0.068)),
                       "n_estimators": int(params.get("n_estimators", 200))},
            "metrics": metrics,
            "n_train": n_tr,
            "n_test": n_te,
            "train_secs": train_secs,
            "features": features,
            "artifact": str(art),
            "ts": time.strftime("%H:%M:%S"),
            "verified": None,
        }
        with _RUNS_LOCK:
            _RUNS.insert(0, run)
        _ACTIVE["status"] = "done"
    except Exception as e:  # surface the error to the UI, don't crash the app
        with _RUNS_LOCK:
            _RUNS.insert(0, {
                "run_id": run_id, "dataset": dataset, "model": model_name,
                "error": str(e), "ts": time.strftime("%H:%M:%S"),
                "metrics": None,
            })
        _ACTIVE["status"] = f"error: {e}"
    finally:
        _ACTIVE["running"] = False
        _TRAIN_LOCK.release()


def start_training(dataset, model_name, params, sample_rows):
    """Kick off a training run on a background thread. One at a time."""
    if not _TRAIN_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "a training job is already running"}
    run_id = uuid.uuid4().hex[:8]
    _ACTIVE.update({"running": True, "status": "queued", "run_id": run_id})
    threading.Thread(
        target=_run_training,
        args=(run_id, dataset, model_name, params, sample_rows),
        daemon=True).start()
    return {"ok": True, "run_id": run_id}


def training_status():
    return dict(_ACTIVE)


def list_runs():
    with _RUNS_LOCK:
        return {"production": PRODUCTION_BASELINE, "runs": list(_RUNS)}


def _find_run(run_id):
    with _RUNS_LOCK:
        for r in _RUNS:
            if r.get("run_id") == run_id:
                return r
    return None


def verify_run(run_id):
    """
    Guardrail: the same principle as scripts/retrain_from_feedback.py —
    a new model may only be promoted if its F1 does not regress against the
    production baseline. Returns a PASS/REJECT verdict with reasoning.
    """
    run = _find_run(run_id)
    if not run or not run.get("metrics"):
        return {"ok": False, "error": "run not found or has no metrics"}

    new_f1 = run["metrics"]["f1"]
    prod_f1 = PRODUCTION_BASELINE["f1"]
    passed = new_f1 >= prod_f1 * GUARDRAIL_MIN_F1_RATIO

    if passed:
        reason = (f"new F1 {new_f1:.3f} ≥ production {prod_f1:.3f} — "
                  f"no regression, safe to register")
    else:
        reason = (f"new F1 {new_f1:.3f} < production {prod_f1:.3f} — "
                  f"REJECTED to protect production (this is the guardrail working)")

    run["verified"] = passed
    return {"ok": True, "passed": passed, "reason": reason,
            "new_f1": new_f1, "production_f1": prod_f1}


def register_run(run_id):
    """
    Register a verified run: copy its artifact to a registered filename and
    return the EXACT kubectl deploy commands a real rollout would run. The
    commands are shown, never executed here — deploying to live pods is a
    deliberate manual step (safety).
    """
    run = _find_run(run_id)
    if not run or not run.get("metrics"):
        return {"ok": False, "error": "run not found"}
    if not run.get("verified"):
        return {"ok": False,
                "error": "run has not passed the guardrail — verify first"}

    registered = MODELS_DIR / f"registered_{run_id}.pkl"
    try:
        src = Path(run["artifact"])
        if src.exists():
            registered.write_bytes(src.read_bytes())
    except Exception as e:
        return {"ok": False, "error": f"could not write artifact: {e}"}

    tag = f"devsecmlops-api:candidate-{run_id}"
    deploy_cmds = [
        f"# 1. bake the registered model into a new image",
        f"docker build -t {tag} .",
        f"# 2. load it into the cluster",
        f"minikube image load {tag}",
        f"# 3. roll the deployment to the new image (zero-downtime)",
        f"kubectl set image deployment/anomaly-api api={tag} -n ml-serving",
        f"# 4. watch the rollout",
        f"kubectl rollout status deployment/anomaly-api -n ml-serving",
    ]
    return {"ok": True, "registered": str(registered), "image_tag": tag,
            "deploy_commands": deploy_cmds}
