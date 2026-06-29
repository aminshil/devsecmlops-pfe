"""
FastAPI anomaly-detection service. Selectable artifact via MODEL_NAME env var.
Reads "__feature_order__" from the baselines file when present (SMD model) so
the request feature vector is built in the same order the model was trained on.
"""
import json
import os
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
MODEL_NAME = os.environ.get("MODEL_NAME", "serving").lower()

if MODEL_NAME == "smd":
    MODEL_PATH = MODELS_DIR / "smd_serving_model.pkl"
    BASELINES_PATH = MODELS_DIR / "smd_serving_baselines.json"
elif MODEL_NAME == "serving":
    MODEL_PATH = MODELS_DIR / "serving_model.pkl"
    BASELINES_PATH = MODELS_DIR / "serving_baselines.json"
else:
    raise RuntimeError(f"Unknown MODEL_NAME={MODEL_NAME!r}; use 'serving' or 'smd'.")

if not MODEL_PATH.exists():
    raise RuntimeError(f"Model not found: {MODEL_PATH}.")
if not BASELINES_PATH.exists():
    raise RuntimeError(f"Baselines not found: {BASELINES_PATH}.")

model = joblib.load(MODEL_PATH)
with open(BASELINES_PATH) as f:
    baselines = json.load(f)

machines_known = sorted(m for m in baselines if not m.startswith("__"))
if "__feature_order__" in baselines:
    FEATURES = baselines["__feature_order__"]
else:
    sample = machines_known[0]
    FEATURES = list(baselines[sample].keys())

app = FastAPI(title=f"DevSecMLOps Anomaly Detector [{MODEL_NAME}]", version="1.2.0")


class Reading(BaseModel):
    machine: str
    metrics: dict[str, float]


def zscore_one(reading: Reading):
    stats = baselines.get(reading.machine, baselines["__global__"])
    vals = []
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
        "status": "ok",
        "model": MODEL_NAME,
        "n_machines": len(machines_known),
        "n_features": len(FEATURES),
        "machines_known_sample": machines_known[:10],
        "feature_order_sample": FEATURES[:5] + (["..."] if len(FEATURES) > 5 else []),
    }


@app.post("/predict")
def predict(reading: Reading):
    X = zscore_one(reading)
    is_anomaly = int(model.predict(X)[0] == -1)
    score = float(-model.score_samples(X)[0])
    z = {c: round(float(v), 2) for c, v in zip(FEATURES, X.values[0])}
    if len(z) > 5:
        head = dict(list(z.items())[:5])
        head["..."] = f"{len(z)} total metrics"
        z = head
    return {
        "machine": reading.machine,
        "model": MODEL_NAME,
        "is_anomaly": is_anomaly,
        "anomaly_score": round(score, 4),
        "z_scores": z,
        "machine_known": reading.machine in baselines,
    }
