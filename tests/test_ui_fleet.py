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


def _fake_prom(verdicts, last_seen):
    """urlopen stand-in: the verdict query and the last-seen query get different answers."""
    def opener(url, *a, **k):
        url = getattr(url, "full_url", url)
        if "api_currently_anomalous" not in url:        # the verdict query joins on last-seen too
            return _Resp({"data": {"result": [{"metric": {"machine": m}, "value": [0, str(ts)]}
                                              for m, ts in last_seen.items()]}})
        return _Resp(_prom(verdicts))
    return opener


def test_fleet_reports_the_pods_verdicts_and_the_graph(client, monkeypatch):
    import time as _t
    now = _t.time()
    monkeypatch.setattr(app_module._ur, "urlopen",
                        _fake_prom([("web-01", "web", 1), ("db-06", "db", 0)],
                                   {"web-01": now - 10, "db-06": now - 600}))
    d = client.get("/ui/fleet", auth=CREDS).json()
    assert d["reachable"] is True
    assert d["machines"]["web-01"]["anomalous"] == 1 and d["machines"]["web-01"]["stale"] is False
    assert d["machines"]["db-06"]["anomalous"] == 0 and d["machines"]["db-06"]["stale"] is True
    assert "router-01" in d["router_states"]
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


def _m(anom, stale=False):
    return {"type": "web", "anomalous": anom, "stale": stale}


def _states(machines, deps=10):
    down = {"router-01": [f"web-{i:02d}" for i in range(1, deps + 1)]}
    return app_module._fleet_router_states(machines, ["router-01"], down)["router-01"]


def test_many_dependents_degraded_behind_a_normal_router_is_a_suspected_gray_failure():
    m = {f"web-{i:02d}": _m(1 if i <= 6 else 0) for i in range(1, 11)} | {"router-01": _m(0)}
    s = _states(m)
    assert s["state"] == "gray_suspected" and "gray failure" in s["message"] and s["anomalous"] == 6


def test_router_and_dependents_anomalous_together_is_a_router_incident():
    m = {f"web-{i:02d}": _m(1 if i <= 5 else 0) for i in range(1, 11)} | {"router-01": _m(1)}
    assert _states(m)["state"] == "router_incident"


def test_dependents_that_stop_reporting_are_unreachable_not_failing():
    m = {f"web-{i:02d}": _m(0, stale=i <= 8) for i in range(1, 11)} | {"router-01": _m(0, stale=True)}
    s = _states(m)
    assert s["state"] == "unreachable" and s["stale"] == 8 and "unreachable, not failing" in s["message"]


def test_a_busy_router_with_healthy_dependents_is_flagged_as_router_only():
    m = {f"web-{i:02d}": _m(0) for i in range(1, 11)} | {"router-01": _m(1)}
    assert _states(m)["state"] == "router_only"


def test_a_few_isolated_faults_are_not_a_pattern():
    m = {f"web-{i:02d}": _m(1 if i <= 2 else 0) for i in range(1, 11)} | {"router-01": _m(0)}
    assert _states(m)["state"] == "ok"


def test_the_agent_stays_silent_while_its_machine_is_unreachable():
    sys.path.insert(0, str(ROOT))
    from monitoring import production_agent
    assert production_agent.reports({"reachable": "1"}) and production_agent.reports({})
    assert not production_agent.reports({"reachable": "0"})


def test_fleet_tab_is_in_the_page(client):
    page = client.get("/ui", auth=CREDS).text
    assert 'data-tab="fleet"' in page and 'id="fleet_body"' in page
    assert "api_currently_anomalous" in page and "function loadFleet(" in page
