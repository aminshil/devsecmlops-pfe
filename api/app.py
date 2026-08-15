"""
FastAPI anomaly-detection service — DevSecMLOps Platform v2.3.0
Selectable artifact via MODEL_NAME env var: telecom (default) | smd | serving

v2.2.0 introduced — per-time-window baselines:
  Each machine has separate baselines for night/morning/afternoon/evening.
  /predict accepts an optional 'hour' (0-23) or 'timestamp' to pick the window.
  If neither is given, falls back to the per-machine (all-day) baseline.

v2.4.0 introduced — root cause analysis:
  /root-cause accepts a batch of currently-anomalous machines and ranks
  them by likelihood of being the true root cause versus a downstream
  victim of a cascading failure, using the network-layer dependency
  graph (router -> downstream machines).

Fallback chain for the z-score baseline:
  1. Per-machine + window   (machine trained, this time window available)
  2. Per-machine (all-day)   (window missing or not supplied)
  3. Per-type               (unknown machine, type hint or known type)
  4. Global                 (completely unknown)

Endpoints:
  GET  /health       — service status + model info
  GET  /machines     — list all known machines with type
  POST /predict      — anomaly score for one machine reading
  POST /root-cause   — rank a batch of anomalous machines by root-cause likelihood
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT       = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
MODEL_NAME = os.environ.get("MODEL_NAME", "telecom").lower()

# v3 threshold tuning: flag as anomaly if P(normal) < PREDICT_THRESHOLD.
# Default 0.5 = original argmax behavior. Higher = more aggressive detection
# (more anomalies caught, more false positives). 0.85 chosen for production
# to match operational priority: catch as many real incidents as possible,
# accept increased false-alarm investigation cost. See README "Engineering
# decisions" section for the full recall/precision sweep.
PREDICT_THRESHOLD = float(os.environ.get("PREDICT_THRESHOLD", "0.85"))

# Per-class thresholds (v4, optional). When PER_CLASS_THRESHOLDS is set to a
# JSON map like {"cpu_spike":0.65,"memory_leak":0.7,...}, the v4 path flags an
# anomaly if ANY non-normal class probability >= that class's own threshold,
# instead of the single P(normal) < PREDICT_THRESHOLD rule. This lets weak
# causes (cascade) use a low bar and clean causes a high one -- tuned on a
# held-out validation split (see README). Absent => single-threshold behavior.
_pct_raw = os.environ.get("PER_CLASS_THRESHOLDS", "")
PER_CLASS_THRESHOLDS = json.loads(_pct_raw) if _pct_raw.strip() else None

# Reported cause when only the IsolationForest safety net flags an anomaly
# (the supervised classifier said normal). Defined once to avoid duplication.
SAFETY_NET_CAUSE = "unknown (flagged by safety-net model only)"

sys.path.insert(0, str(ROOT / "ml-model"))
from root_cause import score_root_causes, load_graph

# Feedback loop: prediction logging + operator verdicts (v2.12.0).
# See README 'Feedback loop and online learning' section for design.
from api.feedback_db import (
    init_db as init_feedback_db,
    insert_prediction,
    update_verdict,
    recent_predictions,
    verdict_stats,
    VALID_VERDICTS,
)

ARTIFACTS = {
    "telecom": ("telecom_serving_model.pkl", "telecom_serving_baselines.json"),
    "smd":     ("smd_serving_model.pkl",     "smd_serving_baselines.json"),
    "serving": ("serving_model.pkl",          "serving_baselines.json"),
}

# v2: dual-model (RandomForest cause classifier + IsolationForest safety net),
# trained on labeled anomaly_type data. See ml-model/train tonight's session.
IS_V2 = MODEL_NAME == "telecom_v2"
IS_V3 = MODEL_NAME == "telecom_v3"

if IS_V2:
    RF_PATH  = MODELS_DIR / "telecom_rf_classifier_v2.pkl"
    ISO_PATH = MODELS_DIR / "telecom_iso_v2.pkl"
    BASELINES_PATH = MODELS_DIR / "telecom_baselines_v2.json"
    for path in (RF_PATH, ISO_PATH, BASELINES_PATH):
        if not path.exists():
            raise RuntimeError(f"v2 artifact not found: {path}")
    rf_model  = joblib.load(RF_PATH)
    iso_model = joblib.load(ISO_PATH)
    model = rf_model  # kept for any code that references `model` generically
    with open(BASELINES_PATH) as f:
        baselines = json.load(f)
elif IS_V3:
    # v3: XGBoost cause classifier (better per-cause recall, 34x smaller than
    # RF, baked directly into the image -- no MinIO fetch dependency needed)
    # + IsolationForest safety net (same as v2, unchanged).
    XGB_PATH = MODELS_DIR / "telecom_xgb_classifier_v2.pkl"
    LE_PATH  = MODELS_DIR / "telecom_xgb_label_encoder_v2.pkl"
    ISO_PATH = MODELS_DIR / "telecom_iso_v2.pkl"
    BASELINES_PATH = MODELS_DIR / "telecom_baselines_v2.json"
    for path in (XGB_PATH, LE_PATH, ISO_PATH, BASELINES_PATH):
        if not path.exists():
            raise RuntimeError(f"v3 artifact not found: {path}")
    xgb_model = joblib.load(XGB_PATH)
    xgb_label_encoder = joblib.load(LE_PATH)
    iso_model = joblib.load(ISO_PATH)
    model = xgb_model
    with open(BASELINES_PATH) as f:
        baselines = json.load(f)

    # v4 rolling features model (optional, loaded if the artifacts exist).
    # When the /predict caller provides a 'history' field, we use v4's
    # 15-feature model instead of v3's 6-feature one. See README section
    # 'Rolling features and gradual-onset detection (v4)'.
    XGB_V4_PATH = MODELS_DIR / "telecom_xgb_v4_rolling.pkl"
    LE_V4_PATH  = MODELS_DIR / "telecom_xgb_v4_rolling_encoder.pkl"
    if XGB_V4_PATH.exists() and LE_V4_PATH.exists():
        xgb_v4_model = joblib.load(XGB_V4_PATH)
        xgb_v4_label_encoder = joblib.load(LE_V4_PATH)
        HAS_V4 = True
    else:
        xgb_v4_model = None
        xgb_v4_label_encoder = None
        HAS_V4 = False

else:
    if MODEL_NAME not in ARTIFACTS:
        raise RuntimeError(
            f"Unknown MODEL_NAME={MODEL_NAME!r}. Choose from: {list(ARTIFACTS)} or 'telecom_v2'"
        )
    model_file, baselines_file = ARTIFACTS[MODEL_NAME]
    MODEL_PATH     = MODELS_DIR / model_file
    BASELINES_PATH = MODELS_DIR / baselines_file
    if not MODEL_PATH.exists():
        raise RuntimeError(f"Model not found: {MODEL_PATH}")
    if not BASELINES_PATH.exists():
        raise RuntimeError(f"Baselines not found: {BASELINES_PATH}")
    model = joblib.load(MODEL_PATH)
    with open(BASELINES_PATH) as f:
        baselines = json.load(f)

HAS_WINDOWS = baselines.get("__has_windows__", False)

machines_known = sorted(
    m for m in baselines
    if not m.startswith("__") and "|" not in m
)
FEATURES = (baselines["__feature_order__"]
            if "__feature_order__" in baselines
            else list(baselines[machines_known[0]].keys()))


def hour_to_window(hour: int) -> str:
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def _resolve_window(reading) -> str | None:
    """Determine the time window from hour or timestamp, if provided."""
    if not HAS_WINDOWS:
        return None
    if reading.hour is not None:
        if not 0 <= reading.hour <= 23:
            raise HTTPException(status_code=400,
                                detail="hour must be between 0 and 23")
        return hour_to_window(reading.hour)
    if reading.timestamp is not None:
        try:
            ts = datetime.fromisoformat(reading.timestamp)
        except ValueError:
            raise HTTPException(status_code=400,
                                detail="timestamp must be ISO format, e.g. 2026-06-29T03:00:00")
        return hour_to_window(ts.hour)
    return None


def _get_stats(machine: str, window: str | None, machine_type: str | None):
    """Fallback chain: machine+window -> machine -> type -> global."""
    if window is not None:
        wkey = f"{machine}|{window}"
        if wkey in baselines:
            return baselines[wkey], "machine+window"
    if machine in baselines:
        return baselines[machine], "machine"
    if machine_type:
        tkey = f"__type__{machine_type}"
        if tkey in baselines:
            return baselines[tkey], "type"
    return baselines["__global__"], "global"


app = FastAPI(
    title=f"DevSecMLOps — Anomaly Detector [{MODEL_NAME}]",
    version="2.19.0",
    description=(
        "Per-machine per-time-window z-score + Isolation Forest anomaly detection. "
        "Trained on a 200-machine synthetic Tunisie Telecom fleet "
        "(11 types, 4 time windows: night/morning/afternoon/evening). "
        "Fallback chain: machine+window -> machine -> type -> global. "
        "Includes dependency-graph-based root cause ranking for cascading failures. "
        "v2.12.0 adds a feedback loop: every /predict call is logged to a "
        "persistent SQLite DB, operators submit verdicts via /feedback/{id}, "
        "and the retrain pipeline uses accumulated feedback to improve the "
        "model over time. See README section 'Feedback loop and online learning'."
    ),
)


# Global flag: is the feedback DB available? Set at startup, checked before
# every write. The model-serving path (/health, /predict predictions) is the
# critical function -- the feedback DB is secondary. If the DB is unreachable
# the API still serves predictions; feedback logging just becomes best-effort.
FEEDBACK_DB_AVAILABLE = False


def _safe_insert_prediction(**kwargs):
    """Best-effort prediction logging: never let a DB failure break /predict."""
    global FEEDBACK_DB_AVAILABLE
    if not FEEDBACK_DB_AVAILABLE:
        return None
    try:
        return insert_prediction(**kwargs)
    except Exception as e:
        print(f"WARNING: feedback DB write failed ({e}). Prediction served without logging.")
        FEEDBACK_DB_AVAILABLE = False
        return None


@app.on_event("startup")
def _init_feedback_db_on_startup():
    """
    Try to initialize the feedback DB. Non-fatal: if the DB is unreachable
    (e.g. PostgreSQL not yet up, or running the container standalone without
    a DB, as in CI smoke tests), log a warning and continue. The API will
    still serve predictions; prediction logging is skipped until the DB
    becomes reachable.
    """
    global FEEDBACK_DB_AVAILABLE
    try:
        init_feedback_db()
        FEEDBACK_DB_AVAILABLE = True
        print("Feedback DB initialized -- prediction logging enabled")
    except Exception as e:
        FEEDBACK_DB_AVAILABLE = False
        print(f"WARNING: feedback DB unreachable at startup ({e}). "
              f"Serving predictions without feedback logging.")


class Reading(BaseModel):
    machine: str
    metrics: dict[str, float]
    machine_type: str | None = None   # optional hint for unknown machines
    hour: int | None = None           # 0-23, picks the time window
    timestamp: str | None = None      # ISO string, alternative to hour
    # v4 rolling features (optional). When present, the caller supplies the
    # recent history of cpu/ram/load_avg so the API can compute rolling
    # mean/std/delta and use the 15-feature v4 model. When absent, the API
    # falls back to the 6-feature v3 model. See README 'Rolling features'.
    # Format: {"cpu": [v1..v10], "ram": [v1..v10], "load_avg": [v1..v10]}
    # where the LAST value in each list is the most recent (current) reading.
    history: dict[str, list[float]] | None = None


class AnomalyBatch(BaseModel):
    anomalies: dict[str, float]   # {machine_name: anomaly_score, ...}


@app.get("/health")
def health():
    return {
        "status":          "ok",
        "model":           MODEL_NAME,
        "version":         "2.19.0",
        "n_machines":      len(machines_known),
        "n_features":      len(FEATURES),
        "features":        FEATURES,
        "has_windows":     HAS_WINDOWS,
        "windows":         baselines.get("__windows__", []),
        "machines_sample": machines_known[:10],
        "machines_total":  len(machines_known),
        "fallback_chain":  ["machine+window", "machine", "type", "global"],
        "root_cause_analysis": True,
    }


@app.get("/machines")
def list_machines():
    """List all known machines with their type."""
    result = []
    for m in machines_known:
        entry = {"machine": m}
        mdata = baselines.get(m, {})
        if "__type__" in mdata:
            entry["type"] = mdata["__type__"]
        result.append(entry)
    return {"machines": result, "total": len(result)}


def _compute_rolling_features(history: dict) -> list[float] | None:
    """
    Compute the 9 v4 rolling features from a caller-supplied history dict.

    history format: {"cpu": [...], "ram": [...], "load_avg": [...]}
    where each list is up to 10 recent readings, LAST value = most recent.

    Returns a 9-element list in the exact order the v4 model expects:
      [cpu_rolling_mean, cpu_rolling_std, cpu_delta,
       ram_rolling_mean, ram_rolling_std, ram_delta,
       load_avg_rolling_mean, load_avg_rolling_std, load_avg_delta]

    Matches ml-model/preprocess.add_rolling_features exactly:
      - rolling_mean: mean of the window
      - rolling_std:  sample std (ddof=1) of the window; 0.0 if < 2 values
      - delta:        last value - first value in the window
        (training used diff(periods=window-1), i.e. current minus the
        value window-1 steps back; with a full 10-value window that is
        history[-1] - history[0])

    Returns None if history is malformed (missing keys / empty lists),
    signalling the caller to fall back to the v3 model.
    """
    import statistics

    required = ("cpu", "ram", "load_avg")
    if not all(k in history and history[k] for k in required):
        return None

    feats = []
    for col in required:
        vals = list(history[col])
        if not vals:
            return None
        rolling_mean = sum(vals) / len(vals)
        rolling_std = statistics.stdev(vals) if len(vals) >= 2 else 0.0
        delta = vals[-1] - vals[0]
        feats.extend([rolling_mean, rolling_std, delta])
    return feats



def _predict_v2(reading, X, z_scores, window, machine_type, baseline_used):
    # RandomForest: predicted cause (normal | cpu_spike | memory_leak | ...)
    cause = str(rf_model.predict(X)[0])
    rf_is_anomaly = cause != "normal"
    
    # IsolationForest: independent unsupervised vote (safety net for
    # patterns the classifier wasn't trained to recognize, e.g. cascade)
    iso_is_anomaly = bool(iso_model.predict(X)[0] == -1)
    iso_score = float(-iso_model.score_samples(X)[0])
    
    is_anomaly = rf_is_anomaly or iso_is_anomaly
    if rf_is_anomaly:
        likely_cause = cause
    elif iso_is_anomaly:
        likely_cause = SAFETY_NET_CAUSE
    else:
        likely_cause = None
    
    return {
        "machine":        reading.machine,
        "machine_type":   machine_type or "unknown",
        "model":          MODEL_NAME,
        "window":         window or "not-supplied",
        "is_anomaly":     int(is_anomaly),
        "likely_cause":   likely_cause,
        "rf_vote":        {"is_anomaly": int(rf_is_anomaly), "cause": cause},
        "iso_vote":       {"is_anomaly": int(iso_is_anomaly), "score": round(iso_score, 4)},
        "z_scores":       z_scores,
        "machine_known":  reading.machine in baselines,
        "baseline_used":  baseline_used,
    }
    

def _decide_v4_anomaly(v4_p_normal, v4_non_normal):
    """
    Decide (is_anomaly, cause) for the v4 path from the class probabilities.

    Per-class mode (PER_CLASS_THRESHOLDS set): anomaly if ANY non-normal class
    clears its own threshold; cause = the class exceeding its bar by the
    largest margin. Single-threshold mode: anomaly if P(normal) 
    PREDICT_THRESHOLD; cause = the top non-normal class. Extracted from
    _predict_v4 to keep that function's cognitive complexity under the limit.
    """
    if PER_CLASS_THRESHOLDS is not None:
        exceed = [(cls, prob, prob - PER_CLASS_THRESHOLDS.get(cls, 1.01))
                  for cls, prob in v4_non_normal
                  if prob >= PER_CLASS_THRESHOLDS.get(cls, 1.01)]
        if exceed:
            return True, max(exceed, key=lambda x: x[2])[0]
        return False, "normal"
    # Single-threshold mode
    if v4_p_normal < PREDICT_THRESHOLD:
        return True, max(v4_non_normal, key=lambda x: x[1])[0]
    return False, "normal"


def _predict_v4(reading, X, z_scores, window, machine_type, baseline_used):
    """
    v4 rolling-features prediction. Returns a response dict, or None if the
    v4 path doesn't apply (v4 not loaded, no history, or malformed history)
    -- in which case the caller falls through to the v3 path.

    Guard clauses keep this flat: bail out early rather than nesting the
    whole body inside two ifs.
    """
    if not HAS_V4 or reading.history is None:
        return None
    rolling_feats = _compute_rolling_features(reading.history)
    if rolling_feats is None:
        return None

    x_v4_df = pd.DataFrame(
        [list(X.to_numpy()[0]) + rolling_feats],
        columns=list(FEATURES) + [
            "cpu_rolling_mean", "cpu_rolling_std", "cpu_delta",
            "ram_rolling_mean", "ram_rolling_std", "ram_delta",
            "load_avg_rolling_mean", "load_avg_rolling_std", "load_avg_delta",
        ],
    )
    v4_proba = xgb_v4_model.predict_proba(x_v4_df.to_numpy())[0]
    v4_classes = list(xgb_v4_label_encoder.classes_)
    v4_normal_idx = v4_classes.index("normal")
    v4_p_normal = float(v4_proba[v4_normal_idx])
    v4_non_normal = [(v4_classes[i], v4_proba[i]) for i in range(len(v4_classes)) if i != v4_normal_idx]
    v4_is_anomaly, v4_cause = _decide_v4_anomaly(v4_p_normal, v4_non_normal)
    # IsolationForest safety net still runs on the base 6 features
    iso_is_anomaly = bool(iso_model.predict(X)[0] == -1)
    iso_score = float(-iso_model.score_samples(X)[0])
    is_anomaly = v4_is_anomaly or iso_is_anomaly
    if v4_is_anomaly:
        likely_cause = v4_cause
    elif iso_is_anomaly:
        likely_cause = SAFETY_NET_CAUSE
    else:
        likely_cause = None
    prediction_id = _safe_insert_prediction(
        machine=reading.machine,
        machine_type=machine_type,
        window=window,
        features=z_scores,
        raw_metrics=reading.metrics,
        model_version="telecom_v4_rolling",
        predict_threshold=PREDICT_THRESHOLD,
        xgb_p_normal=v4_p_normal,
        xgb_cause=v4_cause,
        iso_score=round(iso_score, 4),
        final_is_anomaly=int(is_anomaly),
        final_cause=likely_cause,
    )
    return {
        "prediction_id":  prediction_id,
        "machine":        reading.machine,
        "machine_type":   machine_type or "unknown",
        "model":          "telecom_v4_rolling",
        "window":         window or "not-supplied",
        "is_anomaly":     int(is_anomaly),
        "likely_cause":   likely_cause,
        "xgb_vote":       {"is_anomaly": int(v4_is_anomaly), "cause": v4_cause},
        "iso_vote":       {"is_anomaly": int(iso_is_anomaly), "score": round(iso_score, 4)},
        "z_scores":       z_scores,
        "rolling_features_used": True,
        "machine_known":  reading.machine in baselines,
        "baseline_used":  baseline_used,
    }


def _predict_v3_v4(reading, X, z_scores, window, machine_type, baseline_used):
    # Try the v4 rolling-features path first; if it doesn't apply (no v4,
    # no history, or malformed history) it returns None and we fall through
    # to the standard v3 6-feature path below.
    v4_result = _predict_v4(reading, X, z_scores, window, machine_type, baseline_used)
    if v4_result is not None:
        return v4_result
    
    # XGBoost: predicts numeric class index, decoded back to string cause via
    # LabelEncoder (normal | cpu_spike | memory_leak | network_flood |
    # disk_saturation | silent_failure). Better per-cause recall than the RF
    # primary in v2, especially on memory_leak. 34x smaller model (3.6MB vs
    # 124MB), baked into the image directly -- no MinIO fetch needed.
    # Threshold-tuned prediction (see PREDICT_THRESHOLD at top of file).
    # Flag as anomaly if P(normal) < threshold, i.e. not confident it's normal.
    xgb_proba = xgb_model.predict_proba(X.to_numpy())[0]
    classes = list(xgb_label_encoder.classes_)
    normal_idx = classes.index("normal")
    p_normal = float(xgb_proba[normal_idx])
    xgb_is_anomaly = p_normal < PREDICT_THRESHOLD
    # Reported cause: if not normal, pick the most probable non-normal class
    if xgb_is_anomaly:
        non_normal_scores = [(classes[i], xgb_proba[i]) for i in range(len(classes)) if i != normal_idx]
        cause = max(non_normal_scores, key=lambda x: x[1])[0]
    else:
        cause = "normal"
    
    # IsolationForest: unchanged safety net for novel patterns.
    iso_is_anomaly = bool(iso_model.predict(X)[0] == -1)
    iso_score = float(-iso_model.score_samples(X)[0])
    
    is_anomaly = xgb_is_anomaly or iso_is_anomaly
    if xgb_is_anomaly:
        likely_cause = cause
    elif iso_is_anomaly:
        likely_cause = SAFETY_NET_CAUSE
    else:
        likely_cause = None
    
    prediction_id = _safe_insert_prediction(
        machine=reading.machine,
        machine_type=machine_type,
        window=window,
        features=z_scores,
        raw_metrics=reading.metrics,
        model_version=MODEL_NAME,
        predict_threshold=PREDICT_THRESHOLD,
        xgb_p_normal=p_normal,
        xgb_cause=cause,
        iso_score=round(iso_score, 4),
        final_is_anomaly=int(is_anomaly),
        final_cause=likely_cause,
    )
    return {
        "prediction_id":  prediction_id,
        "machine":        reading.machine,
        "machine_type":   machine_type or "unknown",
        "model":          MODEL_NAME,
        "window":         window or "not-supplied",
        "is_anomaly":     int(is_anomaly),
        "likely_cause":   likely_cause,
        "xgb_vote":       {"is_anomaly": int(xgb_is_anomaly), "cause": cause},
        "iso_vote":       {"is_anomaly": int(iso_is_anomaly), "score": round(iso_score, 4)},
        "z_scores":       z_scores,
        "machine_known":  reading.machine in baselines,
        "baseline_used":  baseline_used,
    }
    

def _predict_fallback(reading, X, z_scores, window, machine_type, baseline_used):
    is_anomaly = int(model.predict(X)[0] == -1)
    score      = float(-model.score_samples(X)[0])
    
    return {
        "machine":        reading.machine,
        "machine_type":   machine_type or "unknown",
        "model":          MODEL_NAME,
        "window":         window or "not-supplied",
        "is_anomaly":     is_anomaly,
        "anomaly_score":  round(score, 4),
        "z_scores":       z_scores,
        "machine_known":  reading.machine in baselines,
        "baseline_used":  baseline_used,   # machine+window | machine | type | global
    }

@app.post(
    "/predict",
    responses={400: {"description": "Invalid input: missing metric, bad hour/timestamp"}},
)
def predict(reading: Reading):
    window = _resolve_window(reading)

    machine_data = baselines.get(reading.machine, {})
    machine_type = reading.machine_type or machine_data.get("__type__")

    stats, baseline_used = _get_stats(reading.machine, window, machine_type)

    vals = []
    for col in FEATURES:
        mean, std = stats.get(col, baselines["__global__"][col])
        raw = reading.metrics.get(col)
        if raw is None:
            raise HTTPException(status_code=400,
                                detail=f"Missing metric: {col}")
        vals.append((raw - mean) / std)

    X        = pd.DataFrame([vals], columns=FEATURES)
    z_scores = {c: round(float(v), 2) for c, v in zip(FEATURES, X.to_numpy()[0])}

    if IS_V2:
        return _predict_v2(reading, X, z_scores, window, machine_type, baseline_used)
    if IS_V3:
        return _predict_v3_v4(reading, X, z_scores, window, machine_type, baseline_used)
    return _predict_fallback(reading, X, z_scores, window, machine_type, baseline_used)


@app.post(
    "/root-cause",
    responses={400: {"description": "Invalid input: empty anomalies dict"}},
)
def root_cause(batch: AnomalyBatch):
    """
    Given a set of currently-anomalous machines (from /predict results),
    rank them by likelihood of being the true root cause versus a
    downstream victim of a cascading failure.

    Uses the network-layer dependency graph (router -> downstream
    machines). Does NOT use service-tier correlation (web->app->db),
    which was tested and found to degrade detection accuracy -- see
    README "cascading failures" section.
    """
    if not batch.anomalies:
        raise HTTPException(status_code=400, detail="anomalies dict cannot be empty")

    graph = load_graph()
    ranked = score_root_causes(batch.anomalies, graph)
    return {
        "input_count": len(batch.anomalies),
        "ranked": ranked,
        "likely_root_causes": [r["machine"] for r in ranked if r["role"] == "likely_root_cause"],
    }


# -----------------------------------------------------------------------------
# Feedback loop endpoints (v2.12.0)
# See README "Feedback loop and online learning" section for the full design.
# -----------------------------------------------------------------------------

class FeedbackIn(BaseModel):
    """Operator verdict on a prior prediction, submitted by prediction_id."""
    verdict: str
    notes: str | None = None


@app.post(
    "/feedback/{prediction_id}",
    responses={
        404: {"description": "prediction_id not found in the feedback DB"},
        400: {"description": "Invalid verdict value"},
    },
)
def submit_feedback(prediction_id: str, feedback: FeedbackIn):
    """
    Submit an operator verdict for a prior prediction.

    verdict must be one of:
      - true_positive   (model correctly flagged an anomaly)
      - false_positive  (model wrongly flagged; it was fine)
      - true_negative   (model said normal, and it was)
      - false_negative  (model missed a real anomaly)

    Idempotent: submitting a second verdict for the same prediction_id
    overwrites the first with the newer timestamp.
    """
    try:
        row = update_verdict(prediction_id, feedback.verdict, feedback.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail=f"prediction_id {prediction_id!r} not found")
    return {
        "prediction_id":     row["id"],
        "operator_verdict":  row["operator_verdict"],
        "verdict_timestamp": row["verdict_timestamp"],
        "verdict_notes":     row["verdict_notes"],
        "original_prediction": {
            "machine":          row["machine"],
            "model_version":    row["model_version"],
            "final_is_anomaly": row["final_is_anomaly"],
            "final_cause":      row["final_cause"],
        },
    }


@app.get("/predictions/recent")
def get_recent_predictions(limit: int = 100):
    """
    Return the N most recent predictions from the feedback DB.
    Defaults to 100, capped at 1000. Useful for inspecting accumulated
    data and for feeding the retrain pipeline.
    """
    return {
        "count": limit,
        "predictions": recent_predictions(limit=limit),
    }


@app.get("/feedback/stats")
def get_feedback_stats():
    """
    Return counts of each verdict type across the whole DB.
    Predictions with no operator verdict yet are grouped under '_pending'.
    Useful for monitoring how much labeled data has been collected for
    the retrain pipeline.
    """
    return verdict_stats()


# ---------------------------------------------------------------------------
# Mission-control UI (convenience layer; does not alter serving logic)
# ---------------------------------------------------------------------------
import subprocess as _sp
import urllib.request as _ur
import socket as _socket
from fastapi.responses import HTMLResponse as _HTMLResponse

_UI_PATH = Path(__file__).resolve().parent / "static" / "control_panel.html"


@app.get("/ui", response_class=_HTMLResponse)
def ui_page():
    """Serve the mission-control web UI. no-store so the browser always
    fetches the latest version (this file changes often during development
    and a stale cached copy silently breaks buttons/JS with no visible error)."""
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
    try:
        return _HTMLResponse(_UI_PATH.read_text(encoding="utf-8"), headers=headers)
    except Exception:
        return _HTMLResponse("<h1>UI not found</h1>", status_code=404, headers=headers)


def _port_open(host, port, timeout=2):
    try:
        with _socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _pod_is_ready(pod):
    """True if a pod has a Ready condition set to True."""
    for c in pod.get("status", {}).get("conditions", []):
        if c.get("type") == "Ready" and c.get("status") == "True":
            return True
    return False


def _k8s_layer():
    """Build the Kubernetes layer status dict (extracted to keep ui_status simple)."""
    try:
        out = _sp.run(["kubectl", "get", "pods", "-n", "ml-serving", "-o", "json"],
                      capture_output=True, text=True, timeout=6)
        if out.returncode != 0:
            return {"layer": "L4", "name": "Kubernetes", "up": False, "detail": "unreachable"}
        pods = json.loads(out.stdout)["items"]
        ready = sum(1 for pod in pods if _pod_is_ready(pod))
        return {"layer": "L4", "name": "Kubernetes",
                "up": ready > 0, "detail": f"{ready}/{len(pods)} pods ready"}
    except Exception:
        return {"layer": "L4", "name": "Kubernetes", "up": False, "detail": "unreachable"}


# (host, port, layer, name, detail) for each port-checked layer
_PORT_LAYERS = [
    ("localhost", 5000, "L2", "Docker registry", ":5000"),
    ("localhost", 8080, "L3", "Jenkins", "CI/CD"),
    ("localhost", 9000, "L3", "SonarQube", "SAST"),
    ("localhost", 9090, "L5", "Prometheus", ":9090"),
    ("localhost", 3000, "L5", "Grafana", ":3000"),
    ("localhost", 9200, "L5", "Replay exporter", ":9200"),
    ("localhost", 9300, "L5", "Anomaly bridge", ":9300"),
    ("localhost", 9100, "L5", "Node exporter", ":9100"),
    ("localhost", 9400, "L5", "K8s exporter", ":9400"),
]


@app.get("/ui/status")
def ui_status():
    """Live health of every platform layer."""
    layers = [{"layer": "L0/L1", "name": "Anomaly API", "up": True,
               "detail": f"{MODEL_NAME} v2.19.0"}]
    for host, port, layer, name, detail in _PORT_LAYERS:
        if layer == "L5":
            continue  # add L5 after the K8s layer for ordering
        layers.append({"layer": layer, "name": name,
                       "up": _port_open(host, port), "detail": detail})
    layers.append(_k8s_layer())
    for host, port, layer, name, detail in _PORT_LAYERS:
        if layer == "L5":
            layers.append({"layer": layer, "name": name,
                           "up": _port_open(host, port), "detail": detail})
    return {"layers": layers, "up": sum(1 for lyr in layers if lyr["up"]), "total": len(layers)}


def _prom_query(expr):
    try:
        url = f"http://localhost:9090/api/v1/query?query={_ur.quote(expr)}"
        with _ur.urlopen(url, timeout=3) as r:
            res = json.loads(r.read())["data"]["result"]
        return float(res[0]["value"][1]) if res else None
    except Exception:
        return None


@app.get("/ui/metrics")
def ui_metrics():
    """Live fleet stats from Prometheus."""
    ac = _prom_query("bridge_anomaly_count")
    cpu = _prom_query("avg(sim_cpu_percent)")
    load = _prom_query("avg(sim_load_avg)")
    gt = _prom_query("sum(sim_ground_truth_anomaly)")
    return {
        "anomaly_count": None if ac is None else int(ac),
        "avg_cpu": None if cpu is None else round(cpu, 1),
        "avg_load": None if load is None else round(load, 1),
        "ground_truth": None if gt is None else int(gt),
    }


# ---------------------------------------------------------------------------
# Infrastructure detail endpoint (for the control-panel Infrastructure tab)
# ---------------------------------------------------------------------------
def _run(cmd, timeout=8):
    try:
        out = _sp.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def _infra_docker_containers():
    containers = []
    raw = _run(["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"])
    for line in raw.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            containers.append({"name": parts[0], "image": parts[1],
                               "status": parts[2], "ports": parts[3] if len(parts) > 3 else ""})
    return containers


def _infra_docker_images():
    images = []
    raw = _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}|{{.Size}}|{{.CreatedSince}}"])
    for line in raw.strip().splitlines():
        if "devsecmlops" not in line:
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            images.append({"image": parts[0], "size": parts[1], "created": parts[2]})
    return images[:12]


def _infra_k8s_pods():
    pods = []
    raw = _run(["kubectl", "get", "pods", "-n", "ml-serving", "-o", "json"])
    if not raw:
        return pods
    try:
        for p in json.loads(raw)["items"]:
            cs = p.get("status", {}).get("containerStatuses", [{}])[0]
            pods.append({
                "name": p["metadata"]["name"],
                "ready": cs.get("ready", False),
                "image": cs.get("image", "?"),
                "restarts": cs.get("restartCount", 0),
                "phase": p["status"].get("phase", "?"),
            })
    except Exception:
        pass
    return pods


def _infra_k8s_usage():
    usage = []
    raw = _run(["kubectl", "top", "pods", "-n", "ml-serving", "--no-headers"])
    for line in raw.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            usage.append({"pod": parts[0], "cpu": parts[1], "memory": parts[2]})
    return usage


def _hpa_cpu_util(status):
    for m in status.get("currentMetrics", []) or []:
        if m.get("resource", {}).get("name") == "cpu":
            return m["resource"]["current"].get("averageUtilization")
    return None


def _infra_k8s_hpa():
    hpa = []
    raw = _run(["kubectl", "get", "hpa", "-n", "ml-serving", "-o", "json"])
    if not raw:
        return hpa
    try:
        for h in json.loads(raw)["items"]:
            s = h.get("status", {})
            hpa.append({
                "name": h["metadata"]["name"],
                "current": s.get("currentReplicas"),
                "desired": s.get("desiredReplicas"),
                "min": h["spec"].get("minReplicas"),
                "max": h["spec"].get("maxReplicas"),
                "cpu_util": _hpa_cpu_util(s),
            })
    except Exception:
        pass
    return hpa


def _infra_prometheus_targets():
    targets = []
    try:
        with _ur.urlopen("http://localhost:9090/api/v1/targets", timeout=4) as r:
            for tgt in json.loads(r.read())["data"]["activeTargets"]:
                targets.append({"job": tgt["labels"].get("job", "?"),
                                "health": tgt.get("health", "?")})
    except Exception:
        pass
    return targets


def _infra_live_anomalies():
    anomalies = []
    try:
        url = "http://localhost:9090/api/v1/query?query=" + _ur.quote("bridge_is_anomaly == 1")
        with _ur.urlopen(url, timeout=4) as r:
            for s in json.loads(r.read())["data"]["result"]:
                m = s["metric"]
                anomalies.append({"machine": m.get("machine", "?"),
                                  "role": m.get("role", "?"), "type": m.get("type", "?")})
    except Exception:
        pass
    return anomalies


@app.get("/ui/infra")
def ui_infra():
    """Detailed Docker + Kubernetes + Prometheus info for the Infrastructure tab."""
    return {
        "docker_containers": _infra_docker_containers(),
        "docker_images": _infra_docker_images(),
        "k8s_pods": _infra_k8s_pods(),
        "k8s_usage": _infra_k8s_usage(),
        "k8s_hpa": _infra_k8s_hpa(),
        "prometheus_targets": _infra_prometheus_targets(),
        "live_anomalies": _infra_live_anomalies(),
    }


# ---------------------------------------------------------------------------
# Real-data sampler for the demo (independent test set, true labels + causes)
# ---------------------------------------------------------------------------
_SAMPLE_CACHE = {"rows": None}


def _read_run(f, header):
    """Read up to 11 consecutive rows for a single machine from the current
    file position. Returns the list of parsed rows (all same machine)."""
    run = []
    first_machine = None
    for _ in range(11):
        line = f.readline().strip()
        if not line:
            break
        parts = line.split(",")
        if len(parts) != len(header):
            continue
        row = dict(zip(header, parts))
        if first_machine is None:
            first_machine = row["machine"]
        if row["machine"] != first_machine:
            break
        run.append(row)
    return run


def _collect_samples(path, header, size, n_seeks=800):
    """Seek to n_seeks positions across the file; at each, read one machine's
    run and keep it if it yields at least 10 rows (current + 10-row history)."""
    samples = []
    with open(path, "r") as f:
        for k in range(n_seeks):
            f.seek(int(size * k / n_seeks))
            f.readline()  # discard partial line
            run = _read_run(f, header)
            if len(run) >= 10:
                samples.append({"cur": run[9], "hist": run[:10]})
    return samples


def _load_sample_pool():
    """Fleet-wide sample from the INDEPENDENT test set (telecom_fleet_v2_test.csv,
    the held-out seed-123 set used for evaluation, never seen in training).
    Seeks across the file so ALL machine types appear, reading a run of
    consecutive same-machine rows at each seek for the v4 rolling history."""
    if _SAMPLE_CACHE["rows"] is not None:
        return _SAMPLE_CACHE["rows"]
    import os as _os
    import random as _random
    _rng = _random.SystemRandom()
    path = Path(__file__).resolve().parent.parent / "data" / "telecom_fleet_v2_test.csv"
    try:
        with open(path, "r") as f:
            header = f.readline().strip().split(",")
        samples = _collect_samples(path, header, _os.path.getsize(path))
        _rng.shuffle(samples)
    except Exception:
        samples = []
    _SAMPLE_CACHE["rows"] = samples
    return samples


@app.get("/ui/sample")
def ui_sample(n: int = 20):
    """N real labeled readings from the independent test set, with true cause."""
    import random as _random
    _rng = _random.SystemRandom()
    pool = _load_sample_pool()
    if not pool:
        return {"rows": [], "error": "test dataset not available"}
    n = max(1, min(n, 100))
    picks = _rng.sample(pool, min(n, len(pool)))
    out = []
    for s in picks:
        try:
            cur = s["cur"]; hist = s["hist"]
            # parse real hour from "YYYY-MM-DD HH:MM:SS" so the model uses the
            # correct per-time-window baseline (hardcoding 14 caused false flags)
            _ts = str(cur.get("timestamp", ""))
            try:
                _hr = int(_ts[11:13])
            except Exception:
                _hr = 14
            out.append({
                "machine": cur["machine"],
                "type": cur.get("type", ""),
                "hour": _hr,
                "metrics": {
                    "cpu": float(cur["cpu"]), "ram": float(cur["ram"]),
                    "network": float(cur["network"]), "disk_io": float(cur["disk_io"]),
                    "disk_usage": float(cur["disk_usage"]), "load_avg": float(cur["load_avg"]),
                },
                "history": {
                    "cpu": [float(h["cpu"]) for h in hist],
                    "ram": [float(h["ram"]) for h in hist],
                    "load_avg": [float(h["load_avg"]) for h in hist],
                },
                "true_label": int(cur["label"]),
                "true_cause": cur.get("anomaly_type", "") or ("normal" if cur["label"] == "0" else "anomaly"),
            })
        except Exception:
            continue
    return {"rows": out}


# ===========================================================================
# Training console (MLOps tab) — pick a dataset, train for real, compare runs,
# verify against the production baseline (guardrail), register + show deploy.
# Backed by api/training.py. This is a demo/ops surface, not the serving path.
# ===========================================================================
from api import training as _training


class _TrainIn(BaseModel):
    dataset: str
    model: str = "isolation_forest"
    contamination: float = 0.068
    n_estimators: int = 200
    sample_rows: int = 20000  # 0 = full dataset


class _RunRef(BaseModel):
    run_id: str


@app.get("/ui/datasets")
def ui_datasets():
    """Real CSVs under data/ that can be trained on."""
    return {"datasets": _training.list_datasets()}


@app.post("/ui/train")
def ui_train(body: _TrainIn):
    """Start a real training run on a background thread (one at a time)."""
    return _training.start_training(
        body.dataset, body.model,
        {"contamination": body.contamination, "n_estimators": body.n_estimators},
        body.sample_rows)


@app.get("/ui/train/status")
def ui_train_status():
    """Poll the active training job."""
    return _training.training_status()


@app.get("/ui/train/runs")
def ui_train_runs():
    """All runs this session + the production baseline (for the comparison table)."""
    return _training.list_runs()


@app.post("/ui/verify")
def ui_verify(body: _RunRef):
    """Guardrail: new F1 must not regress vs production. Returns PASS/REJECT."""
    return _training.verify_run(body.run_id)


@app.post("/ui/register")
def ui_register(body: _RunRef):
    """Register a verified run + return the real kubectl deploy commands (shown)."""
    return _training.register_run(body.run_id)
# ===========================================================================
# Operator-tab helpers + pipeline control (drive the Grafana-feeding pipeline
# and the sim clock from the control panel). Demo/ops surface.
# ===========================================================================
import urllib.parse as _up


def _prom_scalar(metric: str):
    """Read a single scalar metric from Prometheus, or None if unavailable."""
    try:
        url = "http://localhost:9090/api/v1/query?query=" + _up.quote(metric)
        with _ur.urlopen(url, timeout=4) as r:
            data = json.loads(r.read().decode())
        result = data.get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
    except Exception:
        return None
    return None


@app.get("/ui/sim-hour")
def ui_sim_hour():
    """The replay exporter's CURRENT simulated hour (what the model is scored
    against), so the panel shows the simulated clock - not wall-clock time."""
    h = _prom_scalar("sim_replay_hour")
    return {"sim_hour": int(h) if h is not None else None,
            "window": hour_to_window(int(h)) if h is not None else None,
            "source": "replay exporter via Prometheus" if h is not None else "unavailable"}


@app.get("/ui/sample-one")
def ui_sample_one():
    """One real labeled reading from the test set, with its REAL hour parsed
    from the row timestamp - for the Operator tab's 'load a real row' button.
    The window therefore comes from the metric's own time, not an app default."""
    try:
        rows = ui_sample(n=1).get("rows", [])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not rows:
        return {"ok": False, "error": "no sample available (test set missing?)"}
    r = rows[0]
    return {"ok": True, "machine": r["machine"], "hour": r.get("hour"),
            "metrics": r["metrics"], "true_label": r.get("true_label"),
            "true_cause": r.get("true_cause")}


# Processes the panel can see and (re)start - the two that feed Grafana.
_PIPELINE = {
    "replay": {"script": "monitoring/replay_exporter.py", "port": 9200,
               "label": "replay exporter"},
    "bridge": {"script": "monitoring/anomaly_bridge.py", "port": 9300,
               "label": "anomaly bridge"},
}


def _proc_running(script: str) -> bool:
    try:
        out = _sp.run(["pgrep", "-f", script], capture_output=True, text=True)
        return out.returncode == 0 and bool(out.stdout.strip())
    except Exception:
        return False


@app.get("/ui/pipeline")
def ui_pipeline_status():
    """Status of the Grafana-feeding pipeline processes + the sim hour."""
    status = {}
    for key, p in _PIPELINE.items():
        status[key] = {"label": p["label"], "port": p["port"],
                       "running": _proc_running(p["script"])}
    h = _prom_scalar("sim_replay_hour")
    return {"processes": status,
            "sim_hour": int(h) if h is not None else None}


class _PipelineAction(BaseModel):
    target: str
    action: str


@app.post("/ui/pipeline")
def ui_pipeline_control(body: _PipelineAction):
    """Start or stop a pipeline process from the panel. Guarded so 'start'
    never spawns a duplicate. Demo/ops convenience - the processes otherwise
    run via start_control_panel.sh or the Ansible monitoring role."""
    p = _PIPELINE.get(body.target)
    if not p:
        return {"ok": False, "error": f"unknown target '{body.target}'"}
    script = p["script"]

    if body.action == "start":
        if _proc_running(script):
            return {"ok": True, "note": f"{p['label']} already running", "running": True}
        try:
            logf = open(f"/tmp/{body.target}.log", "ab")
            _sp.Popen(["python3", script], stdout=logf, stderr=logf, cwd=str(ROOT))
            return {"ok": True, "note": f"{p['label']} started", "running": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if body.action == "stop":
        try:
            _sp.run(["pkill", "-f", script], capture_output=True)
            return {"ok": True, "note": f"{p['label']} stopped", "running": False}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"unknown action '{body.action}'"}
# ===========================================================================
# Per-layer recovery ("Fix" button). A FIXED allow-list maps each layer name
# to its known-correct recovery command — no arbitrary commands can run.
# Container layers -> docker start; process layers -> nohup start (guarded);
# Kubernetes -> background minikube start --force (non-blocking, ~2min).
# ===========================================================================

# layer name -> ("docker", container) | ("process", script) | ("k8s", None)
_FIX_MAP = {
    "Docker registry":  ("docker", "registry"),
    "Jenkins":          ("docker", "jenkins"),
    "SonarQube":        ("docker", "sonarqube"),
    "Grafana":          ("docker", "grafana"),
    "Node exporter":    ("docker", "node-exporter"),
    "Prometheus":       ("process", "prometheus"),
    "Replay exporter":  ("process", "monitoring/replay_exporter.py"),
    "Anomaly bridge":   ("process", "monitoring/anomaly_bridge.py"),
    "K8s exporter":     ("process", "monitoring/k8s_exporter.py"),
    "Kubernetes":       ("k8s", None),
}


class _FixAction(BaseModel):
    layer: str              # the layer 'name' from /ui/status
    action: str = "start"   # "start" (fix) or "stop"


@app.post("/ui/fix")
def ui_fix(body: _FixAction):
    """Run the known recovery for one down layer. Non-blocking for Kubernetes."""
    def _ret(ok, note=None, error=None, **extra):
        _log_fix_action(body.layer, body.action, ok, note or error or "")
        out = {"ok": ok}
        if note is not None: out["note"] = note
        if error is not None: out["error"] = error
        out.update(extra)
        return out

    entry = _FIX_MAP.get(body.layer)
    if not entry:
        return _ret(False, error=f"no known fix for '{body.layer}'")
    kind, target = entry

    # --- STOP action (container/process layers only; never K8s) ---
    if body.action == "stop":
        if kind == "k8s":
            return _ret(False, error="stopping Kubernetes from the panel is disabled (too disruptive)")
        if kind == "docker":
            try:
                r = _sp.run(["docker", "stop", target], capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    return _ret(True, note=f"stopped container '{target}'")
                return _ret(False, error=r.stderr.strip() or f"docker stop {target} failed")
            except Exception as e:
                return _ret(False, error=str(e))
        if kind == "process":
            pat = "prometheus --config" if target == "prometheus" else target
            try:
                _sp.run(["pkill", "-f", pat], capture_output=True)
                return _ret(True, note=f"stopped {body.layer}")
            except Exception as e:
                return _ret(False, error=str(e))
        return _ret(False, error="unknown stop kind")

    if kind == "docker":
        try:
            r = _sp.run(["docker", "start", target], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return _ret(True, note=f"started container '{target}' — may take a few seconds to be ready")
            return _ret(False, error=r.stderr.strip() or f"docker start {target} failed")
        except Exception as e:
            return _ret(False, error=str(e))

    if kind == "process":
        if target == "prometheus":
            script_check = "prometheus --config"
        else:
            script_check = target
        try:
            chk = _sp.run(["pgrep", "-f", script_check], capture_output=True, text=True)
            if chk.returncode == 0 and chk.stdout.strip():
                return _ret(True, note=f"{body.layer} already running")
        except Exception:
            pass
        try:
            logf = open(f"/tmp/fix_{body.layer.replace(' ', '_')}.log", "ab")
            if target == "prometheus":
                _sp.Popen(["prometheus", "--config.file=monitoring/prometheus.yml",
                           "--storage.tsdb.path=/tmp/prom_data"],
                          stdout=logf, stderr=logf, cwd=str(ROOT))
            else:
                _sp.Popen(["python3", target], stdout=logf, stderr=logf, cwd=str(ROOT))
            return _ret(True, note=f"{body.layer} started")
        except Exception as e:
            return _ret(False, error=str(e))

    if kind == "k8s":
        script = os.path.join(str(ROOT), "scripts", "k8s_recover.sh")
        if not os.path.exists(script):
            return _ret(False, error="recovery script not found at scripts/k8s_recover.sh")
        try:
            logf = open("/tmp/k8s_recover.log", "ab")
            _sp.Popen(["bash", script], stdout=logf, stderr=logf)
            return _ret(True, note="Kubernetes full recovery started in the background "
                    "(~1-4 min: tries a gentle start, then a full rebuild if wedged). "
                    "Keep demoing other tabs; K8s goes green when it finishes. "
                    "Progress is in /tmp/k8s_recover.log. If even this fails, Docker "
                    "itself may be down — that needs the terminal.", **{"async": True})
        except Exception as e:
            return _ret(False, error=str(e))

    return {"ok": False, "error": "unknown fix kind"}


# ===========================================================================
# Action log for Fix/Stop (Status tab visibility). In-memory, most-recent
# first, capped so it doesn't grow unbounded.
# ===========================================================================
import datetime as _dt

_FIX_LOG = []
_FIX_LOG_MAX = 50


def _log_fix_action(layer: str, action: str, ok: bool, note: str):
    _FIX_LOG.insert(0, {
        "time": _dt.datetime.now().strftime("%H:%M:%S"),
        "layer": layer,
        "action": action,
        "ok": ok,
        "note": note,
    })
    del _FIX_LOG[_FIX_LOG_MAX:]


@app.get("/ui/fix-log")
def ui_fix_log():
    return {"entries": _FIX_LOG}


# ===========================================================================
# Live progress tail for an in-flight Fix/Stop action - lets the panel show
# real lines from the action's own log file while it runs, not just a final
# result. Read-only, capped to the last N lines.
# ===========================================================================
_ACTION_LOGFILES = {
    "Kubernetes": "/tmp/k8s_recover.log",
}


def _action_logfile_for(layer: str) -> str:
    if layer in _ACTION_LOGFILES:
        return _ACTION_LOGFILES[layer]
    return f"/tmp/fix_{layer.replace(' ', '_')}.log"


@app.get("/ui/fix-progress")
def ui_fix_progress(layer: str, lines: int = 12):
    """Tail the log file for one layer's in-flight action (read-only)."""
    path = _action_logfile_for(layer)
    if not os.path.exists(path):
        return {"lines": [], "exists": False}
    try:
        with open(path, "r", errors="replace") as f:
            content = f.readlines()
        tail = [ln.rstrip("\n") for ln in content[-lines:]]
        return {"lines": tail, "exists": True}
    except Exception as e:
        return {"lines": [f"(could not read log: {e})"], "exists": True}
