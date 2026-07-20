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
    version="2.12.0",
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


@app.on_event("startup")
def _init_feedback_db_on_startup():
    """Ensure the feedback DB and its table exist before serving traffic."""
    init_feedback_db()


class Reading(BaseModel):
    machine: str
    metrics: dict[str, float]
    machine_type: str | None = None   # optional hint for unknown machines
    hour: int | None = None           # 0-23, picks the time window
    timestamp: str | None = None      # ISO string, alternative to hour


class AnomalyBatch(BaseModel):
    anomalies: dict[str, float]   # {machine_name: anomaly_score, ...}


@app.get("/health")
def health():
    return {
        "status":          "ok",
        "model":           MODEL_NAME,
        "version":         "2.4.0",
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
            likely_cause = "unknown (flagged by safety-net model only)"
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

    if IS_V3:
        # XGBoost: predicts numeric class index, decoded back to string cause via
        # LabelEncoder (normal | cpu_spike | memory_leak | network_flood |
        # disk_saturation | silent_failure). Better per-cause recall than the RF
        # primary in v2, especially on memory_leak. 34x smaller model (3.6MB vs
        # 124MB), baked into the image directly -- no MinIO fetch needed.
        # Threshold-tuned prediction (see PREDICT_THRESHOLD at top of file).
        # Flag as anomaly if P(normal) < threshold, i.e. not confident it's normal.
        xgb_proba = xgb_model.predict_proba(X.values)[0]
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
            likely_cause = "unknown (flagged by safety-net model only)"
        else:
            likely_cause = None

        prediction_id = insert_prediction(
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
