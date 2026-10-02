"""Continuous-training panel routes: cluster proxies, the read-only tab data, and the removed /ui/register.
The cluster API is mocked; the routes sit under /ui/, so they must also require the ops credentials."""
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

PID = "5609efb6-f543-4c09-9305-7b2de09a3912"
CREDS = ("ct-test-user", "ct-test-password-0123456789")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", True)
    monkeypatch.setattr(app_module, "_OPS_UI_USER", CREDS[0])
    monkeypatch.setattr(app_module, "_OPS_UI_PASSWORD", CREDS[1])
    return TestClient(app_module.app)


@pytest.fixture
def cluster(monkeypatch):
    """Replace the cluster call with a recorder; tests set .answers[(method, path)]."""
    class Fake:
        calls, answers = [], {}

        def __call__(self, method, path, body=None, timeout=15):
            self.calls.append((method, path, body))
            answer = self.answers.get((method, path), {})
            if isinstance(answer, Exception):
                raise answer
            return answer

    fake = Fake()
    fake.calls, fake.answers = [], {}
    monkeypatch.setattr(app_module, "_cluster_call", fake)
    return fake


def test_cluster_routes_need_the_ops_credentials(client, cluster):
    for method, path in (("post", "/ui/cluster/predict"), ("post", f"/ui/cluster/feedback/{PID}"), ("get", "/ui/ct")):
        assert getattr(client, method)(path).status_code == 401, path
    assert cluster.calls == []


def test_cluster_predict_is_forwarded_without_empty_fields(client, cluster):
    cluster.answers[("POST", "/predict")] = {"prediction_id": PID, "is_anomaly": 1, "model": "telecom_v4_rolling"}
    r = client.post("/ui/cluster/predict", auth=CREDS,
                    json={"machine": "web-01", "hour": 14, "metrics": {"cpu": 30}, "history": None})
    assert r.status_code == 200 and r.json()["prediction_id"] == PID
    assert cluster.calls == [("POST", "/predict", {"machine": "web-01", "hour": 14, "metrics": {"cpu": 30}})]


def test_cluster_predict_reports_an_unreachable_cluster(client, cluster):
    cluster.answers[("POST", "/predict")] = app_module._ClusterError("cluster API unreachable")
    r = client.post("/ui/cluster/predict", auth=CREDS, json={"machine": "web-01", "metrics": {}})
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]


def test_cluster_feedback_forwards_a_valid_verdict(client, cluster):
    cluster.answers[("POST", f"/feedback/{PID}")] = {"prediction_id": PID, "operator_verdict": "true_positive"}
    r = client.post(f"/ui/cluster/feedback/{PID}", auth=CREDS, json={"verdict": "true_positive", "notes": "x" * 500})
    assert r.status_code == 200
    method, path, body = cluster.calls[0]
    assert (method, path) == ("POST", f"/feedback/{PID}")
    assert body["verdict"] == "true_positive" and len(body["notes"]) == 200


@pytest.mark.parametrize("pid", ["not-a-uuid", "../../etc/passwd", "5609efb6"])
def test_cluster_feedback_rejects_an_id_that_is_not_a_uuid(client, cluster, pid):
    r = client.post(f"/ui/cluster/feedback/{pid}", auth=CREDS, json={"verdict": "true_positive"})
    assert r.status_code in (400, 404)
    assert cluster.calls == []


def test_cluster_feedback_rejects_an_unknown_verdict(client, cluster):
    r = client.post(f"/ui/cluster/feedback/{PID}", auth=CREDS, json={"verdict": "great"})
    assert r.status_code == 400 and cluster.calls == []


def test_ct_survives_an_unreachable_cluster(client, cluster, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "MANIFEST_PATH", tmp_path / "missing.json")
    err = app_module._ClusterError("cluster API unreachable")
    cluster.answers[("GET", "/feedback/stats")] = err
    cluster.answers[("GET", "/health")] = err
    r = client.get("/ui/ct", auth=CREDS)
    assert r.status_code == 200
    body = r.json()
    assert body["feedback"]["reachable"] is False and "unreachable" in body["feedback"]["error"]
    assert body["deployed"] == {} and body["history"] == []


def test_ct_summarises_feedback_and_history_newest_first(client, cluster, monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "production": {"decision_threshold": 0.6, "source": "adopted", "evaluation": {"v4": {"f1": 0.7246}}},
        "history": [
            {"finished_at": "t1", "git_commit": "a" * 40, "guardrail_passed": True, "promoted": True},
            {"finished_at": "t2", "git_commit": "b" * 40, "guardrail_passed": False, "promoted_to_workspace": False,
             "candidate_f1": {"v4": 0.68}, "candidate_threshold": 0.6, "production_threshold": 0.6,
             "datasets": {"train": {}, "test": {}, "feedback_0": {}}},
        ]}))
    monkeypatch.setattr(app_module, "MANIFEST_PATH", manifest)
    cluster.answers[("GET", "/feedback/stats")] = {"total_predictions": 100, "by_verdict": {"true_positive": 3, "_pending": 97}}
    cluster.answers[("GET", "/health")] = {"version": "2.20.3", "model_lineage": {"served_v4_f1": 0.7246}}
    body = client.get("/ui/ct", auth=CREDS).json()
    assert body["feedback"]["judged"] == 3 and body["feedback"]["pending"] == 97
    assert body["feedback"]["by_verdict"] == {"true_positive": 3}
    assert body["deployed"]["version"] == "2.20.3"
    assert body["manifest"]["served_v4_f1"] == 0.7246 and body["manifest"]["threshold"] == 0.6
    assert [h["finished_at"] for h in body["history"]] == ["t2", "t1"]
    assert body["history"][0]["feedback_files"] == 1 and body["history"][0]["promoted"] is False
    assert body["history"][1]["promoted"] is True            # entries written before the key was renamed


def test_the_dead_register_endpoint_is_gone(client):
    assert client.post("/ui/register", auth=CREDS, json={"run_id": "x"}).status_code == 404
