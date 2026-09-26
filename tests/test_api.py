"""
Tests for the FastAPI serving layer (api/app.py).
Uses FastAPI's TestClient -- no live server or network needed.

Runs against MODEL_NAME=telecom_v3, the production serving path: XGBoost
cause classifier + IsolationForest safety net, switching to the v4
rolling-features model when the request carries `history`.
"""
import base64
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ["MODEL_NAME"] = "telecom_v3"
os.environ["ENABLE_OPS_UI"] = "1"
os.environ["OPS_UI_USER"] = "tester"
os.environ["OPS_UI_PASSWORD"] = "correct-horse-battery"
# unreachable DB: exercises the "serve predictions without logging" path
os.environ["DATABASE_URL"] = "postgresql://x:x@127.0.0.1:9/none"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
import api.app as app_module
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


# ---------------------------------------------------------------------------
# Production path: v3 without history, v4 with history
# ---------------------------------------------------------------------------
NORMAL = {"cpu": 30, "ram": 50, "network": 80,
          "disk_io": 25, "disk_usage": 40, "load_avg": 1.2}


def _history(n=10):
    return {"cpu": [30.0] * n, "ram": [50.0] * n, "load_avg": [1.2] * n}


def test_predict_without_history_uses_v3():
    body = client.post("/predict", json={"machine": "web-01", "hour": 14,
                                         "metrics": NORMAL}).json()
    assert body["model"] == "telecom_v3"
    assert 0.0 <= body["xgb_vote"]["p_anomaly"] <= 1.0


def test_predict_with_history_uses_v4():
    body = client.post("/predict", json={"machine": "web-01", "hour": 14,
                                         "metrics": NORMAL,
                                         "history": _history()}).json()
    assert body["model"] == "telecom_v4_rolling"
    assert body["rolling_features_used"] is True


@pytest.mark.parametrize("n", [5, 9, 11])
def test_history_must_match_training_window(n):
    resp = client.post("/predict", json={"machine": "web-01", "hour": 14,
                                         "metrics": NORMAL, "history": _history(n)})
    assert resp.status_code == 422


def test_history_rejects_missing_keys():
    hist = _history(); del hist["ram"]
    resp = client.post("/predict", json={"machine": "web-01", "hour": 14,
                                         "metrics": NORMAL, "history": hist})
    assert resp.status_code == 422


def test_v4_decision_threshold():
    # p_normal below PREDICT_THRESHOLD -> anomalous, with the top cause
    is_anom, cause = app_module._decide_v4_anomaly(
        0.10, [("cpu_spike", 0.8), ("memory_leak", 0.1)])
    assert is_anom and cause == "cpu_spike"
    is_anom, cause = app_module._decide_v4_anomaly(
        0.99, [("cpu_spike", 0.005), ("memory_leak", 0.005)])
    assert not is_anom


# ---------------------------------------------------------------------------
# Training / serving consistency for the v4 rolling features
# ---------------------------------------------------------------------------
def test_rolling_features_match_training_pipeline():
    """The serving code computes rolling features from caller-supplied
    history; training computes them with pandas over the time series. Both
    must produce the same numbers for a full 10-reading window."""
    import numpy as np
    import pandas as pd
    sys.path.insert(0, str(ROOT / "ml-model"))
    from preprocess import add_rolling_features, rolling_feature_names

    rng = np.random.default_rng(0)
    n = 25
    df = pd.DataFrame({
        "machine": ["web-01"] * n,
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="30s"),
        "cpu": rng.normal(40, 8, n), "ram": rng.normal(60, 5, n),
        "load_avg": rng.normal(2, 0.4, n),
    })
    trained = add_rolling_features(df)
    last = trained.iloc[-1]
    hist = {c: df[c].iloc[-10:].tolist() for c in ("cpu", "ram", "load_avg")}
    served = app_module._compute_rolling_features(hist)
    expected = [last[name] for name in rolling_feature_names()]
    assert np.allclose(served, expected, rtol=1e-9, atol=1e-9)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
def test_metrics_counter_increments_and_labels_are_bounded():
    def count(model):
        text = client.get("/metrics").text
        total = 0.0
        for line in text.splitlines():
            if line.startswith("api_predictions_total{") and f'model="{model}"' in line:
                total += float(line.rsplit(" ", 1)[1])
        return total
    before = count("telecom_v3")
    client.post("/predict", json={"machine": "web-01", "hour": 14, "metrics": NORMAL})
    assert count("telecom_v3") == before + 1

    client.post("/predict", json={"machine": "random-name-xyz-123", "hour": 14,
                                  "metrics": NORMAL})
    text = client.get("/metrics").text
    assert "random-name-xyz-123" not in text


def test_request_counter_uses_route_templates():
    client.post("/feedback/abc-123", json={"verdict": "true_positive"})
    text = client.get("/metrics").text
    assert 'route="/feedback/{prediction_id}"' in text
    assert "abc-123" not in text


# ---------------------------------------------------------------------------
# Feedback DB unavailable: predictions still served, feedback endpoints 503
# ---------------------------------------------------------------------------
def test_predict_served_when_db_unavailable():
    body = client.post("/predict", json={"machine": "web-01", "hour": 14,
                                         "metrics": NORMAL}).json()
    assert body["prediction_id"] is None
    assert body["is_anomaly"] in (0, 1)


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/feedback/abc", {"verdict": "true_positive"}),
    ("get", "/predictions/recent", None),
    ("get", "/feedback/stats", None),
])
def test_feedback_endpoints_return_503_without_db(method, path, payload):
    resp = getattr(client, method)(path, json=payload) if payload else getattr(client, method)(path)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Operations UI access control
# ---------------------------------------------------------------------------
def _basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_ops_ui_requires_authentication():
    resp = client.get("/ui/status")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic")


def test_ops_ui_rejects_wrong_password():
    assert client.get("/ui/datasets", headers=_basic("tester", "wrong")).status_code == 401


def test_ops_ui_accepts_correct_credentials():
    resp = client.get("/ui/datasets", headers=_basic("tester", "correct-horse-battery"))
    assert resp.status_code == 200


def test_ops_ui_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", False)
    assert client.get("/ui/status", headers=_basic("tester", "correct-horse-battery")).status_code == 404


def test_serving_endpoints_need_no_ops_credentials():
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


# ---------------------------------------------------------------------------
# Model integrity: a file that differs from the promoted artifact is refused
# ---------------------------------------------------------------------------
def test_artifact_integrity_check_rejects_modified_file(tmp_path, monkeypatch):
    import json as _json
    manifest = _json.loads(app_module.MANIFEST_PATH.read_text())
    rel = next(iter(manifest["production"]["artifacts"]))
    manifest["production"]["artifacts"][rel] = "0" * 64
    fake = tmp_path / "manifest.json"
    fake.write_text(_json.dumps(manifest))
    monkeypatch.setattr(app_module, "MANIFEST_PATH", fake)
    with pytest.raises(RuntimeError, match="integrity"):
        app_module._verify_artifact_integrity()
