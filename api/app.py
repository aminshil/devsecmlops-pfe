"""
FastAPI anomaly-detection service — DevSecMLOps Platform v2.1.0
Selectable artifact via MODEL_NAME env var: telecom (default) | smd | serving

Fallback chain for unknown machines:
  1. Per-machine baseline  (trained machine)
  2. Per-type baseline     (e.g. machine_type=web → __type__web average)
  3. Global baseline       (fleet-wide average)

Endpoints:
  GET  /health     — service status + model info
  GET  /machines   — list all known machines with type
  POST /predict    — anomaly score for one machine reading
"""
import json
import os
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT       = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
MODEL_NAME = os.environ.get("MODEL_NAME", "telecom").lower()

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

machines_known = sorted(m for m in baselines
                        if not m.startswith("__"))
FEATURES = (baselines["__feature_order__"]
            if "__feature_order__" in baselines
            else list(baselines[machines_known[0]].keys()))


def _get_stats(machine: str, machine_type: str | None):
    """Fallback chain: per-machine → per-type → global."""
    if machine in baselines:
        return baselines[machine], "machine"
    if machine_type:
        type_key = f"__type__{machine_type}"
        if type_key in baselines:
            return baselines[type_key], "type"
    return baselines["__global__"], "global"


app = FastAPI(
    title=f"DevSecMLOps — Anomaly Detector [{MODEL_NAME}]",
    version="2.1.0",
    description=(
        "Per-machine z-score + Isolation Forest anomaly detection. "
        "Trained on a 200-machine synthetic Tunisie Telecom fleet "
        "(11 types: web/app/db/cache/queue/batch/edge/router/firewall/dns/voip). "
        "Fallback chain: per-machine → per-type → global."
    ),
)


class Reading(BaseModel):
    machine: str
    metrics: dict[str, float]
    machine_type: str | None = None   # optional hint for unknown machines


@app.get("/health")
def health():
    return {
        "status":          "ok",
        "model":           MODEL_NAME,
        "version":         "2.1.0",
        "n_machines":      len(machines_known),
        "n_features":      len(FEATURES),
        "features":        FEATURES,
        "machines_sample": machines_known[:10],
        "machines_total":  len(machines_known),
        "fallback_chain":  ["per-machine", "per-type", "global"],
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
    # Resolve machine type: from request hint or from baselines
    machine_data = baselines.get(reading.machine, {})
    machine_type = reading.machine_type or machine_data.get("__type__")

    stats, baseline_used = _get_stats(reading.machine, machine_type)

    # Build z-score vector
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
                  for c, v in zip(FEATURES, X.values[0])}

    return {
        "machine":        reading.machine,
        "machine_type":   machine_type or "unknown",
        "model":          MODEL_NAME,
        "is_anomaly":     is_anomaly,
        "anomaly_score":  round(score, 4),
        "z_scores":       z_scores,
        "machine_known":  reading.machine in baselines,
        "baseline_used":  baseline_used,   # machine | type | global
    }
