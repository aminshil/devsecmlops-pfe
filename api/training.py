"""
Experiment console backend for the control panel (MLOps tab).

Scope: the unsupervised safety-net component (IsolationForest). The operator
picks a training dataset, a random sample size and hyperparameters, trains
for real, and compares the candidate against the PRODUCTION IsolationForest
evaluated on the same held-out rows of the independent test set, each model
z-scoring with its own baselines -- a like-for-like comparison.

This console does not promote or deploy anything. The only path to
production is the model pipeline (ml-model/train_production.py, run by the
Jenkins model job), which retrains all serving components together, applies
the guardrail on the full independent test set and records the lineage.
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

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"

TEST_DATASET = "telecom_fleet_v2_test.csv"   # independent evaluation set (seed 123)
EVAL_ROWS = 200_000                           # held-out rows used by every comparison
PRODUCTION_ISO = MODELS_DIR / "telecom_iso_v2.pkl"
PRODUCTION_BASELINES = MODELS_DIR / "telecom_baselines_v2.json"

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


def _resolve_dataset(dataset: str) -> Path:
    """Only files listed by list_datasets() are accepted: the name is matched
    against that list, never joined into a path, so '../', absolute paths
    and other files cannot be read through this API."""
    allowed = {d["name"] for d in list_datasets()}
    if dataset not in allowed:
        raise ValueError(f"unknown dataset: {dataset!r}")
    return DATA_DIR / dataset


def _load_sampled(dataset: str, sample_rows: int, seed: int = 42):
    """
    Uniform random sample of about sample_rows rows across the WHOLE file
    (not the first N rows, which would cover only the first machines/days of a
    time-ordered file). Rows are kept with probability sample_rows/total while
    streaming, so memory stays bounded. sample_rows<=0 reads the full file.
    Returns (df, note).
    """
    # NOSONAR (python:S2245): random, not secrets -- used only for reproducible
    # statistical row sampling (seeded for determinism), never for anything
    # security-sensitive. A cryptographic RNG would be the wrong tool here:
    # it cannot be seeded, so the "seed 42" reproducibility this function
    # documents would be impossible.
    import random
    path = _resolve_dataset(dataset)
    if not sample_rows or sample_rows <= 0:
        df = pd.read_csv(path)
        return df, f"full dataset ({len(df):,} rows)"
    with open(path, "rb") as f:
        total = sum(1 for _ in f) - 1
    frac = min(1.0, sample_rows / max(total, 1))
    rng = random.Random(seed)
    df = pd.read_csv(path, skiprows=lambda i: i > 0 and rng.random() >= frac)
    return df, f"uniform random sample of {len(df):,} / {total:,} rows (seed {seed})"


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


def _eval_iso(model, baselines, features, df_eval):
    from preprocess import apply_zscore  # noqa: E402
    x = apply_zscore(df_eval, baselines, features).to_numpy()
    y_true = df_eval["label"].astype(int).to_numpy()
    y_pred = (model.predict(x) == -1).astype(int)
    y_score = -model.score_samples(x)
    m = {
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
    }
    try:
        m["roc_auc"] = round(float(roc_auc_score(y_true, y_score)), 4)
    except ValueError:
        m["roc_auc"] = None
    return m


def _train_isoforest(df, features, params):
    """Train on the chosen sample; evaluate the candidate AND the production
    IsolationForest on the same held-out rows of the independent test set."""
    from preprocess import add_window_column, build_baselines  # noqa: E402

    if "label" not in df.columns:
        raise ValueError("dataset has no 'label' column to evaluate against")
    if "machine" not in df.columns:
        raise ValueError("dataset has no 'machine' column (per-machine baselines required)")

    baselines = build_baselines(df, features)
    from preprocess import apply_zscore  # noqa: E402
    x_tr = apply_zscore(df, baselines, features).to_numpy()
    contamination = float(params.get("contamination", df["label"].mean() or 0.068))
    model = IsolationForest(contamination=contamination,
                            n_estimators=int(params.get("n_estimators", 200)),
                            random_state=42, n_jobs=-1)
    model.fit(x_tr)

    df_eval, _ = _load_sampled(TEST_DATASET, EVAL_ROWS, seed=123)
    df_eval = add_window_column(df_eval)
    metrics = _eval_iso(model, baselines, features, df_eval)
    prod = _eval_iso(joblib.load(PRODUCTION_ISO), json.load(open(PRODUCTION_BASELINES)),
                     features, df_eval)
    return model, baselines, metrics, prod, len(df), len(df_eval)


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
            model, baselines, metrics, prod_metrics, n_tr, n_te = _train_isoforest(
                df, features, params)
        else:
            raise ValueError("this console trains the IsolationForest safety net only; "
                             "the full serving model is trained by ml-model/train_production.py")
        train_secs = round(time.time() - t0, 1)

        # experiment artifact, outside models/ (never served)
        art = ROOT / "build" / "experiments" / f"run_{run_id}.pkl"
        art.parent.mkdir(parents=True, exist_ok=True)
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
            "production_metrics": prod_metrics,
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
        return {"runs": list(_RUNS)}


def _find_run(run_id):
    with _RUNS_LOCK:
        for r in _RUNS:
            if r.get("run_id") == run_id:
                return r
    return None


def verify_run(run_id):
    """Compare the candidate with the production IsolationForest measured on
    the same held-out rows in the same run. Informational: a better safety
    net is promoted by retraining through the model pipeline, not from here."""
    run = _find_run(run_id)
    if not run or not run.get("metrics"):
        return {"ok": False, "error": "run not found or has no metrics"}
    new_f1 = run["metrics"]["f1"]
    prod_f1 = run["production_metrics"]["f1"]
    better = new_f1 >= prod_f1
    reason = (f"candidate F1 {new_f1:.3f} vs production IsolationForest {prod_f1:.3f} "
              f"on the same {run['n_test']:,} independent test rows -- "
              + ("candidate is at least as good; retrain through the model pipeline to promote"
                 if better else "candidate is worse; production keeps its safety net"))
    run["verified"] = better
    run["verify_reason"] = reason
    return {"ok": True, "passed": better, "reason": reason,
            "new_f1": new_f1, "production_f1": prod_f1}
