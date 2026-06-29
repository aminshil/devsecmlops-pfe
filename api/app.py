"""
FastAPI anomaly-detection service.
Selectable artifact via MODEL_NAME env var: serving | smd | telecom (default)

Telecom artifact: 200-machine fleet, 3 features (cpu/ram/network),
                  day/night patterns, 11 machine types.
SMD artifact    : 28 machines, 37 anonymized features.
Serving artifact: 3-machine synthetic POC (cpu/ram/network).
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
    raise RuntimeError(f"Unknown MODEL_NAME={MODEL_NAME!r}. Choose: {list(ARTIFACTS)}")

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

machines_known = sorted(m for m in baselines if not m.startswith("__"))
FEATURES = (baselines["__feature_order__"]
            if "__feature_order__" in baselines
            else list(baselines[machines_known[0]].keys()))

app = FastAPI(
    title=f"DevSecMLOps — Anomaly Detector [{MODEL_NAME}]",
    version="2.0.0",
    description="Per-machine z-score + Isolation Forest anomaly detection. "
                "Trained on a 200-machine synthetic Tunisie Telecom fleet.",
)


class Reading(BaseModel):
    machine: str
    metrics: dict[str, float]


def zscore_one(reading: Reading) -> pd.DataFrame:
    stats = baselines.get(reading.machine, baselines["__global__"])
    vals  = []
    for col in FEATURES:
        mean, std = stats.get(col, baselines["__global__"][col])
        raw = reading.metrics.get(col)
        if raw is None:
            raise HTTPException(status_code=400, detail=f"Missing metric: {col}")
        vals.append((raw - mean) / std)
    return pd.DataFrame([vals], columns=FEATURES)


@app.get("/health")
def health():
    return {
        "status":                "ok",
        "model":                 MODEL_NAME,
        "version":               "2.0.0",
        "n_machines":            len(machines_known),
        "n_features":            len(FEATURES),
        "features":              FEATURES,
        "machines_sample":       machines_known[:10],
        "machines_total":        len(machines_known),
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
    X          = zscore_one(reading)
    is_anomaly = int(model.predict(X)[0] == -1)
    score      = float(-model.score_samples(X)[0])
    z          = {c: round(float(v), 2) for c, v in zip(FEATURES, X.values[0])}

    # Get machine type if known
    machine_data = baselines.get(reading.machine, {})
    machine_type = machine_data.get("__type__", "unknown")

    return {
        "machine":       reading.machine,
        "machine_type":  machine_type,
        "model":         MODEL_NAME,
        "is_anomaly":    is_anomaly,
        "anomaly_score": round(score, 4),
        "z_scores":      z,
        "machine_known": reading.machine in baselines,
    }
