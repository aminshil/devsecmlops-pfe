"""Notifications card (Infrastructure tab): mute and unmute Telegram through Alertmanager silences.
Alertmanager is replaced by a recorder; the routes sit under /ui/, so they need the ops credentials."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENABLE_OPS_UI", "1")
os.environ.setdefault("OPS_UI_PASSWORD", "test-password-0123456789")

from fastapi.testclient import TestClient  # noqa: E402
import api.app as app_module  # noqa: E402

CREDS = ("al-test-user", "al-test-password-0123456789")
ANOMALIES = [{"name": "alertname", "value": "AnomalyDetected", "isRegex": False, "isEqual": True}]
EVERYTHING = [{"name": "alertname", "value": ".+", "isRegex": True, "isEqual": True}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", True)
    monkeypatch.setattr(app_module, "_OPS_UI_USER", CREDS[0])
    monkeypatch.setattr(app_module, "_OPS_UI_PASSWORD", CREDS[1])
    return TestClient(app_module.app)


@pytest.fixture
def am(monkeypatch):
    """Replace the Alertmanager call with a recorder; tests set .answers[(method, path)]."""
    class Fake:
        def __init__(self):
            self.calls, self.answers = [], {}

        def __call__(self, method, path, body=None, timeout=5):
            self.calls.append((method, path, body))
            answer = self.answers.get((method, path), {})
            if isinstance(answer, Exception):
                raise answer
            return answer

    fake = Fake()
    monkeypatch.setattr(app_module, "_am_call", fake)
    return fake


def _silence(sid, matchers, author="control-panel", state="active", ends="2026-10-22T12:00:00Z"):
    return {"id": sid, "matchers": matchers, "createdBy": author, "status": {"state": state}, "endsAt": ends}


def test_the_notification_routes_need_the_ops_credentials(client, am):
    for method, path in (("get", "/ui/alerting"), ("post", "/ui/alerting/mute"), ("post", "/ui/alerting/unmute")):
        assert getattr(client, method)(path).status_code == 401
    assert am.calls == []


def test_status_shows_only_the_panels_active_silences_and_counts_firing_alerts(client, am):
    am.answers[("GET", "/api/v2/silences")] = [
        _silence("s1", ANOMALIES),                                   # ours, active -> anomalies muted
        _silence("s2", EVERYTHING, author="someone-else"),           # not ours -> ignored
        _silence("s3", EVERYTHING, state="expired"),                 # ours but over -> ignored
    ]
    am.answers[("GET", "/api/v2/alerts")] = [
        {"labels": {"alertname": "AnomalyDetected"}, "status": {"state": "suppressed"}},
        {"labels": {"alertname": "AnomalyDetected"}, "status": {"state": "active"}},
        {"labels": {"alertname": "LayerDown"}, "status": {"state": "active"}},
        {"labels": {"alertname": "LayerDown"}, "status": {"state": "unprocessed"}},
    ]
    d = client.get("/ui/alerting", auth=CREDS).json()
    assert set(d["muted"]) == {"anomalies"} and d["muted"]["anomalies"]["ids"] == ["s1"]
    assert d["firing"] == {"AnomalyDetected": 2, "LayerDown": 1}
    assert set(d["scopes"]) == {"anomalies", "all"}


def test_mute_creates_a_timed_silence_signed_by_the_panel(client, am):
    am.answers[("POST", "/api/v2/silences")] = {"silenceID": "new-1"}
    before = datetime.now(timezone.utc)
    r = client.post("/ui/alerting/mute", auth=CREDS, json={"scope": "anomalies", "hours": 8})
    assert r.status_code == 200 and r.json()["silence_id"] == "new-1"
    method, path, body = am.calls[-1]
    assert (method, path) == ("POST", "/api/v2/silences")
    assert body["matchers"] == ANOMALIES and body["createdBy"] == "control-panel"
    span = datetime.fromisoformat(body["endsAt"]) - datetime.fromisoformat(body["startsAt"])
    assert span == timedelta(hours=8) and datetime.fromisoformat(body["startsAt"]) >= before
    assert any(e["layer"] == "Notifications" and e["action"] == "mute anomalies" for e in app_module._FIX_LOG)


@pytest.mark.parametrize("payload", [{"scope": "anomalies", "hours": 0}, {"scope": "anomalies", "hours": 721},
                                     {"scope": "nothing", "hours": 1}, {"hours": 1}])
def test_mute_refuses_a_bad_scope_or_duration(client, am, payload):
    assert client.post("/ui/alerting/mute", auth=CREDS, json=payload).status_code == 422
    assert am.calls == []


def test_unmute_expires_only_the_panels_silences_for_that_scope(client, am):
    am.answers[("GET", "/api/v2/silences")] = [
        _silence("a1", EVERYTHING), _silence("a2", EVERYTHING),
        _silence("b1", ANOMALIES),                                   # other scope -> kept
        _silence("c1", EVERYTHING, author="someone-else"),           # not ours -> kept
    ]
    r = client.post("/ui/alerting/unmute", auth=CREDS, json={"scope": "all"})
    assert r.status_code == 200 and r.json() == {"expired": 2}
    deleted = [p for m, p, _ in am.calls if m == "DELETE"]
    assert deleted == ["/api/v2/silence/a1", "/api/v2/silence/a2"]


def test_alertmanager_down_is_a_502_not_a_crash(client, am):
    am.answers[("GET", "/api/v2/silences")] = app_module._AlertmanagerError("connection refused")
    assert client.get("/ui/alerting", auth=CREDS).status_code == 502
    assert client.post("/ui/alerting/unmute", auth=CREDS, json={"scope": "all"}).status_code == 502
    am.answers[("POST", "/api/v2/silences")] = app_module._AlertmanagerError("connection refused")
    assert client.post("/ui/alerting/mute", auth=CREDS, json={"scope": "all", "hours": 1}).status_code == 502


def test_the_card_is_on_the_infrastructure_tab_and_uses_these_routes(client):
    page = (ROOT / "api" / "static" / "control_panel.html").read_text()
    assert 'id="alerting_body"' in page and "function loadAlerting(" in page
    assert '"/ui/alerting"' in page and '"/ui/alerting/"+kind' in page
    assert 'loadInfra(); loadPipeline(); loadAlerting();' in page
