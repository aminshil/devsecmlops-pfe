"""The Fleet tab: the live topology view coloured by the models' own verdicts."""
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

CREDS = ("fleet-test-user", "fleet-test-password-0123456789")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", True)
    monkeypatch.setattr(app_module, "_OPS_UI_USER", CREDS[0])
    monkeypatch.setattr(app_module, "_OPS_UI_PASSWORD", CREDS[1])
    return TestClient(app_module.app)


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _prom(series):
    return {"data": {"result": [{"metric": {"machine": m, "machine_type": t}, "value": [0, str(v)]}
                                for m, t, v in series]}}


def test_fleet_needs_the_ops_credentials(client):
    assert client.get("/ui/fleet").status_code == 401


def test_fleet_reports_the_pods_verdicts_and_the_graph(client, monkeypatch):
    monkeypatch.setattr(app_module._ur, "urlopen",
                        lambda *a, **k: _Resp(_prom([("web-01", "web", 1), ("db-06", "db", 0)])))
    d = client.get("/ui/fleet", auth=CREDS).json()
    assert d["reachable"] is True
    assert d["machines"]["web-01"] == {"type": "web", "anomalous": 1}
    assert d["machines"]["db-06"]["anomalous"] == 0
    assert "router-01" in d["routers"] and d["depends_on"]["web-01"] == "router-01"
    assert "web-01" in d["downstream_of"]["router-01"]


def test_fleet_survives_prometheus_being_down(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(app_module._ur, "urlopen", boom)
    d = client.get("/ui/fleet", auth=CREDS).json()
    assert d["reachable"] is False and d["machines"] == {}
    assert "connection refused" in d["error"]
    assert d["routers"], "the topology must still be returned so the tab can draw it"


def test_fleet_tab_is_in_the_page(client):
    page = client.get("/ui", auth=CREDS).text
    assert 'data-tab="fleet"' in page and 'id="fleet_body"' in page
    assert "api_currently_anomalous" in page and "function loadFleet(" in page
