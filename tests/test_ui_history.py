"""History tab: past anomaly episodes from Prometheus. Prometheus is replaced by a recorder returning a
range-query matrix; the route sits under /ui/, so it needs the ops credentials."""
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENABLE_OPS_UI", "1")
os.environ.setdefault("OPS_UI_PASSWORD", "test-password-0123456789")

from fastapi.testclient import TestClient  # noqa: E402
import api.app as app_module  # noqa: E402

CREDS = ("hs-test-user", "hs-test-password-0123456789")
NOW = 1_800_000_000.0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", True)
    monkeypatch.setattr(app_module, "_OPS_UI_USER", CREDS[0])
    monkeypatch.setattr(app_module, "_OPS_UI_PASSWORD", CREDS[1])
    monkeypatch.setattr(app_module.time, "time", lambda: NOW)
    return TestClient(app_module.app)


@pytest.fixture
def prom(monkeypatch):
    """Replace the range query with a recorder; tests set .matrix (or .error)."""
    class Fake:
        def __init__(self):
            self.calls, self.matrix, self.error = [], [], None

        def __call__(self, expr, start, end, step, timeout=30):
            self.calls.append((expr, start, end, step))
            if self.error:
                raise self.error
            return self.matrix

    fake = Fake()
    monkeypatch.setattr(app_module, "_prom_range", fake)
    return fake


def _series(machine, mtype, times):
    return {"metric": {"machine": machine, "machine_type": mtype}, "values": [[t, "1"] for t in times]}


def test_the_history_route_needs_the_ops_credentials(client, prom):
    assert client.get("/ui/history").status_code == 401
    assert prom.calls == []


def test_episodes_split_on_a_gap_and_the_last_one_is_ongoing(client, prom):
    t0 = NOW - 3600
    prom.matrix = [
        # web-01: 4 samples, a 5-minute gap, then anomalous up to now -> 2 episodes, the second ongoing
        _series("web-01", "web", [t0, t0 + 15, t0 + 30, t0 + 45] + [t0 + 345 + 15 * i for i in range(217)]),
        # db-02: one sample (a flicker) -> one 15 s episode, over
        _series("db-02", "db", [t0 + 600]),
    ]
    d = client.get("/ui/history", params={"hours": 1}, auth=CREDS).json()
    expr, start, end, step = prom.calls[-1]
    assert expr == app_module._HISTORY_EXPR and (start, end, step) == (NOW - 3600, NOW, 15)
    eps = d["episodes"]
    by_start = sorted(eps, key=lambda e: e["start"])
    assert [(e["machine"], e["duration_s"], e["ongoing"]) for e in by_start] == [
        ("web-01", 60, False), ("web-01", 217 * 15, True), ("db-02", 15, False)]
    assert [e["start"] for e in eps] == sorted((e["start"] for e in eps), reverse=True)   # newest first
    web = next(r for r in d["machines"] if r["machine"] == "web-01")
    assert (web["episodes"], web["anomalous_s"], web["longest_s"], web["ongoing"]) == (2, 60 + 3255, 3255, True)
    assert d["machines"][0]["machine"] == "web-01"          # most time anomalous first
    assert d["total_episodes"] == 3 and d["truncated"] == 0 and d["step_s"] == 15


def test_the_step_keeps_every_series_under_prometheus_point_limit():
    for hours in (1, 6, 24, 41, 42, 168):
        step = app_module._history_step(hours)
        assert step % 15 == 0 and hours * 3600 / step <= app_module._HISTORY_MAX_POINTS
    assert app_module._history_step(24) == 15 and app_module._history_step(168) == 75


@pytest.mark.parametrize("hours", [0, 169, "x"])
def test_a_window_outside_1_to_168_hours_is_refused(client, prom, hours):
    assert client.get("/ui/history", params={"hours": hours}, auth=CREDS).status_code == 422
    assert prom.calls == []


def test_the_episode_list_is_capped_and_says_how_many_are_left_out(client, prom, monkeypatch):
    monkeypatch.setattr(app_module, "_HISTORY_MAX_EPISODES", 2)
    prom.matrix = [_series("web-01", "web", [NOW - 3000, NOW - 2000, NOW - 1000])]
    d = client.get("/ui/history", auth=CREDS).json()
    assert len(d["episodes"]) == 2 and d["truncated"] == 1 and d["total_episodes"] == 3
    assert d["machines"][0]["episodes"] == 3             # the summary still counts every episode


def test_prometheus_down_is_a_502_not_a_crash(client, prom):
    prom.error = app_module._PrometheusError("connection refused")
    assert client.get("/ui/history", auth=CREDS).status_code == 502


def test_the_history_reads_the_same_state_as_the_alerts():
    rules = yaml.safe_load((ROOT / "monitoring" / "alerting" / "alert_rules.yml").read_text())
    fresh = next(r["expr"] for g in rules["groups"] for r in g["rules"] if r.get("record") == "fleet:machine_anomalous:fresh")
    assert re.sub(r"\s+", "", app_module._FRESH_VERDICT_EXPR) == re.sub(r"\s+", "", fresh)


def test_the_tab_is_on_the_page_and_uses_this_route():
    page = (ROOT / "api" / "static" / "control_panel.html").read_text()
    assert 'data-tab="history"' in page and 'id="history_body"' in page
    assert '"/ui/history?hours="' in page and 'if(id==="history"){ loadHistory(); }' in page


def test_the_history_query_is_the_one_promtool_runs_against_the_alert():
    tested = {t["expr"] for case in yaml.safe_load((ROOT / "monitoring" / "alerting" / "alert_rules_test.yml").read_text())["tests"]
              for t in case.get("promql_expr_test", [])}
    assert app_module._HISTORY_EXPR in tested
