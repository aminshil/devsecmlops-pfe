"""
Tests for the FastAPI serving layer (api/app.py).
Uses FastAPI's TestClient -- no live server or network needed.
Runs against MODEL_NAME=telecom (v1) since that's what's baked into
every Docker image; v2 dual-model behavior is validated separately
via scripts/live_k8s_demo_test.py against the real deployment.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("MODEL_NAME", "telecom")
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from api.app import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["n_features"] == 6
    assert set(body["features"]) == {"cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"}


def test_machines_endpoint_lists_known_machines():
    resp = client.get("/machines")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    assert "machine" in body["machines"][0]


def test_predict_valid_request_returns_200():
    resp = client.post("/predict", json={
        "machine": "web-01",
        "hour": 14,
        "metrics": {
            "cpu": 30, "ram": 50, "network": 80,
            "disk_io": 25, "disk_usage": 40, "load_avg": 1.2
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "is_anomaly" in body
    assert body["is_anomaly"] in (0, 1)
    assert "z_scores" in body
    assert set(body["z_scores"].keys()) == {"cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"}


def test_predict_missing_metric_returns_400():
    resp = client.post("/predict", json={
        "machine": "web-01",
        "hour": 14,
        "metrics": {
            "cpu": 30, "ram": 50, "network": 80,
            "disk_io": 25, "disk_usage": 40
            # load_avg deliberately missing
        }
    })
    assert resp.status_code == 400


def test_predict_unknown_machine_falls_back_to_global():
    resp = client.post("/predict", json={
        "machine": "totally-unknown-machine-xyz",
        "hour": 14,
        "metrics": {
            "cpu": 30, "ram": 50, "network": 80,
            "disk_io": 25, "disk_usage": 40, "load_avg": 1.2
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["machine_known"] is False
    assert body["baseline_used"] == "global"


def test_predict_extreme_disk_saturation_flags_anomaly():
    """Sanity check: an obviously extreme reading should be flagged."""
    resp = client.post("/predict", json={
        "machine": "web-01",
        "hour": 14,
        "metrics": {
            "cpu": 45, "ram": 55, "network": 90,
            "disk_io": 95, "disk_usage": 98, "load_avg": 3.5
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_anomaly"] == 1


def test_root_cause_endpoint_ranks_machines():
    resp = client.post("/root-cause", json={
        "anomalies": {
            "router-01": 0.55,
            "web-01": 0.62,
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "likely_root_causes" in body
