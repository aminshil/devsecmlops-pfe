"""Review tab details and the real-label column: the label store, the Demo-tab route, the review payload
and the production agent's recording. The cluster API is mocked."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENABLE_OPS_UI", "1")
os.environ.setdefault("OPS_UI_PASSWORD", "test-password-0123456789")

from fastapi.testclient import TestClient  # noqa: E402
import api.app as app_module  # noqa: E402
from api import demo_labels  # noqa: E402
from monitoring import production_agent  # noqa: E402

PID = "5609efb6-f543-4c09-9305-7b2de09a3912"
PID2 = "a1b2c3d4-0000-4000-8000-000000000002"
CREDS = ("rd-test-user", "rd-test-password-0123456789")


@pytest.fixture(autouse=True)
def labels_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_LABELS_PATH", str(tmp_path / "labels.jsonl"))
    return tmp_path / "labels.jsonl"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", True)
    monkeypatch.setattr(app_module, "_OPS_UI_USER", CREDS[0])
    monkeypatch.setattr(app_module, "_OPS_UI_PASSWORD", CREDS[1])
    return TestClient(app_module.app)


@pytest.fixture
def cluster(monkeypatch):
    class Fake:
        calls, answers = [], {}

        def __call__(self, method, path, body=None, timeout=15):
            self.calls.append((method, path, body))
            return self.answers.get((method, path), {})

    fake = Fake()
    fake.calls, fake.answers = [], {}
    monkeypatch.setattr(app_module, "_cluster_call", fake)
    return fake


def test_label_store_round_trip_and_rejections():
    assert demo_labels.record(PID, 1, "cpu_spike", "demo tab")
    assert demo_labels.record(PID2, 0, "", "production agent")
    assert not demo_labels.record(None, 1, "x", "demo tab")
    assert not demo_labels.record(PID, 2, "x", "demo tab")
    got = demo_labels.load()
    assert got[PID]["label"] == 1 and got[PID]["cause"] == "cpu_spike"
    assert got[PID2]["label"] == 0 and got[PID2]["source"] == "production agent"


def test_label_store_survives_garbage_and_missing_file(labels_file):
    assert demo_labels.load() == {}
    labels_file.write_text("not json\n" + json.dumps({"id": PID, "label": 1}) + "\n[1]\n")
    assert list(demo_labels.load()) == [PID]


def test_label_store_drops_oldest_lines_when_too_big(monkeypatch):
    monkeypatch.setattr(demo_labels, "MAX_BYTES", 200)
    monkeypatch.setattr(demo_labels, "KEEP_LINES", 3)
    for i in range(20):
        demo_labels.record(f"id-{i}", 1, "c", "demo tab")
    ids = list(demo_labels.load())
    assert len(ids) <= 5 and "id-19" in ids and "id-0" not in ids


def test_demo_predict_strips_the_truth_and_records_it_for_flagged_readings(client, cluster):
    cluster.answers[("POST", "/predict")] = {"prediction_id": PID, "is_anomaly": 1}
    body = {"machine": "web-01", "hour": 14, "metrics": {"cpu": 30}, "true_label": 1, "true_cause": "cpu_spike"}
    assert client.post("/ui/cluster/predict", auth=CREDS, json=body).status_code == 200
    assert cluster.calls == [("POST", "/predict", {"machine": "web-01", "hour": 14, "metrics": {"cpu": 30}})]
    assert demo_labels.load()[PID]["cause"] == "cpu_spike"


def test_demo_predict_records_nothing_for_unflagged_or_unlabelled_readings(client, cluster):
    cluster.answers[("POST", "/predict")] = {"prediction_id": PID, "is_anomaly": 0}
    client.post("/ui/cluster/predict", auth=CREDS, json={"machine": "m", "metrics": {}, "true_label": 0})
    cluster.answers[("POST", "/predict")] = {"prediction_id": PID2, "is_anomaly": 1}
    client.post("/ui/cluster/predict", auth=CREDS, json={"machine": "m", "metrics": {}})
    assert demo_labels.load() == {}


def test_demo_predict_rejects_a_label_that_is_not_0_or_1(client, cluster):
    r = client.post("/ui/cluster/predict", auth=CREDS, json={"machine": "m", "metrics": {}, "true_label": 7})
    assert r.status_code == 422 and cluster.calls == []


def _logged(pid, **over):
    row = {"id": pid, "timestamp": "2026-10-20T01:00:00", "machine": "web-01", "machine_type": "web",
           "time_window": "night", "model_version": "telecom_v4_rolling", "final_is_anomaly": 1,
           "final_cause": "cpu_spike", "xgb_cause": "cpu_spike", "xgb_p_normal": 0.12, "predict_threshold": 0.91,
           "iso_score": 0.61, "operator_verdict": None,
           "raw_metrics_json": json.dumps({"cpu": 97.0, "ram": 40.0}),
           "features_json": json.dumps({"cpu_z": 8.1}),
           "history_json": json.dumps({"cpu": [30.0] * 10, "ram": [40.0] * 10, "load_avg": [1.0] * 10})}
    row.update(over)
    return row


def test_review_returns_details_and_the_real_label(client, cluster):
    demo_labels.record(PID, 1, "cpu_spike", "demo tab")
    cluster.answers[("GET", "/predictions/recent?limit=1000")] = {"predictions": [_logged(PID)]}
    row = client.get("/ui/cluster/review", auth=CREDS).json()["anomalies"][0]
    assert row["metrics"] == {"cpu": 97.0, "ram": 40.0} and row["features"] == {"cpu_z": 8.1}
    assert row["history"]["cpu"] == [30.0] * 10
    assert row["machine_type"] == "web" and row["window"] == "night" and row["iso_score"] == 0.61
    assert row["flagged_by"] == "classifier"
    assert row["real"] == {"label": 1, "cause": "cpu_spike", "source": "demo tab"}


def test_review_without_a_recorded_label_or_with_broken_json_still_works(client, cluster):
    safety = app_module.SAFETY_NET_CAUSE
    cluster.answers[("GET", "/predictions/recent?limit=1000")] = {"predictions": [
        _logged(PID2, final_cause=safety, raw_metrics_json="{broken", features_json=None, history_json="")]}
    row = client.get("/ui/cluster/review", auth=CREDS).json()["anomalies"][0]
    assert row["real"] is None and row["metrics"] is None and row["features"] is None and row["history"] is None
    assert row["flagged_by"] == "safety net"


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _agent_row(label):
    return {"machine": "web-01", "type": "web", "timestamp": "2026-01-01 03:00:00", "label": label,
            "anomaly_type": "cpu_spike" if label == "1" else "",
            **{c: "1.0" for c in production_agent.FEATURES}}


def test_the_agent_records_the_label_of_flagged_readings_only(monkeypatch):
    answers = iter([{"prediction_id": PID, "is_anomaly": 1}, {"prediction_id": PID2, "is_anomaly": 0},
                    {"prediction_id": "id-3", "is_anomaly": 1}])
    monkeypatch.setattr(production_agent.urllib.request, "urlopen", lambda *a, **k: _Resp(next(answers)))
    production_agent.send_one("http://x", _agent_row("1"))
    production_agent.send_one("http://x", _agent_row("1"))
    production_agent.send_one("http://x", _agent_row("0"))
    got = demo_labels.load()
    assert set(got) == {PID, "id-3"}
    assert got[PID]["label"] == 1 and got[PID]["cause"] == "cpu_spike" and got[PID]["source"] == "production agent"
    assert got["id-3"]["label"] == 0 and got["id-3"]["cause"] == "normal"
