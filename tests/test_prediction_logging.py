"""A prediction is logged together with the history the request carried: without it, operator feedback on a v4
prediction cannot become a v4 training example (found when a Jenkins smoke run used rows_used_v4 = 0)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
import api.app as app_module  # noqa: E402

METRICS = {"cpu": 30, "ram": 50, "network": 80, "disk_io": 25, "disk_usage": 40, "load_avg": 1.2}
HISTORY = {"cpu": [30.0 + i for i in range(10)], "ram": [50.0] * 10, "load_avg": [1.2] * 10}


def _post(monkeypatch, body):
    captured = []
    monkeypatch.setattr(app_module, "_safe_insert_prediction", lambda **kw: captured.append(kw) or "pid-1")
    r = TestClient(app_module.app).post("/predict", json=body)
    assert r.status_code == 200, r.text
    assert len(captured) == 1
    return r.json(), captured[0]


def test_the_history_of_a_v4_request_is_logged(monkeypatch):
    out, kw = _post(monkeypatch, {"machine": "web-01", "hour": 14, "metrics": METRICS, "history": HISTORY})
    assert out["model"] == "telecom_v4_rolling"
    assert kw["history"] == HISTORY


def test_a_request_without_history_logs_none(monkeypatch):
    out, kw = _post(monkeypatch, {"machine": "web-01", "hour": 14, "metrics": METRICS})
    assert out["model"] == "telecom_v3"
    assert kw.get("history") is None
