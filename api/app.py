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

sys.path.insert(0, str(ROOT / "ml-model"))
from root_cause import score_root_causes, load_graph

ARTIFACTS = {
    "telecom": ("telecom_serving_model.pkl", "telecom_serving_baselines.json"),
    "smd":     ("smd_serving_model.pkl",     "smd_serving_baselines.json"),
    "serving": ("serving_model.pkl",          "serving_baselines.json"),
}

if MODEL_NAME not in ARTIFACTS:
    raise RuntimeError(
        f"Unknown MODEL_NAME={MODEL_NAME!r}. Choose from: {list(ARTIFACTS)}"
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
    version="2.4.0",
    description=(
        "Per-machine per-time-window z-score + Isolation Forest anomaly detection. "
        "Trained on a 200-machine synthetic Tunisie Telecom fleet "
        "(11 types, 4 time windows: night/morning/afternoon/evening). "
        "Fallback chain: machine+window -> machine -> type -> global. "
        "Includes dependency-graph-based root cause ranking for cascading failures."
    ),
)


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


@app.post("/predict")
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

    X          = pd.DataFrame([vals], columns=FEATURES)
    is_anomaly = int(model.predict(X)[0] == -1)
    score      = float(-model.score_samples(X)[0])
    z_scores   = {c: round(float(v), 2)
                  for c, v in zip(FEATURES, X.to_numpy()[0])}

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


@app.post("/root-cause")
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
