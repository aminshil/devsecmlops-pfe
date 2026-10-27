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
    def no_cluster(*a, **k):
        raise app_module._ClusterError("no cluster in this test")
    monkeypatch.setattr(app_module, "_cluster_call", no_cluster)      # never reach a real NodePort from a test
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
    assert 'id="hs_sort"' in page and 'hsMachines(hsSort(machines,"machine"))' in page   # order choice, latest first
    assert "HS_PAGE_SIZE=25" in page and 'data-hsview="episodes"' in page and 'data-hspick="' in page   # sub-tabs, pages
    assert 'id="hs_now"' in page and "hsRestart" in page


def test_the_history_query_is_the_one_promtool_runs_against_the_alert():
    tested = {t["expr"] for case in yaml.safe_load((ROOT / "monitoring" / "alerting" / "alert_rules_test.yml").read_text())["tests"]
              for t in case.get("promql_expr_test", [])}
    assert app_module._HISTORY_EXPR in tested


# ---- Details: one episode ----

@pytest.fixture
def labels(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_LABELS_PATH", str(tmp_path / "labels.jsonl"))
    from api import demo_labels
    return demo_labels


class _ByQuery:
    """Range-query stand-in that answers by which gauge the query reads."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, expr, start, end, step, timeout=30):
        self.calls.append((expr, start, end, step))
        for key, matrix in self.answers.items():
            if key in expr:
                return matrix
        return []


def test_episode_details_read_the_freshest_pod_for_that_machine_and_its_recorded_labels(client, monkeypatch, labels):
    start, end = NOW - 600, NOW - 300
    for i, t in enumerate([start + 5, start + 35, end + 200]):          # the third is after the episode
        monkeypatch.setattr(labels.time, "time", lambda t=t: t)
        labels.record(f"id-{i}", [1, 0, 1][i], "cpu_spike", "production agent", f"2026-06-29 0{i}:00:00", "web-01")
    monkeypatch.setattr(app_module.time, "time", lambda: NOW)
    fake = _ByQuery({
        "api_input_metric": [{"metric": {"feature": "cpu"}, "values": [[start - 15, "20"], [start, "91"], [end, "40"]]}],
        "api_classifier_p_anomaly": [{"metric": {}, "values": [[start, "0.97"], [end, "0.2"]]}],
        "api_iso_score": [{"metric": {}, "values": [[start, "0.61"]]}],
        "api_anomaly_cause_p": [{"metric": {"cause": "cpu_spike"}, "values": [[start + 15 * i, "0.9"] for i in range(10)]},
                                {"metric": {"cause": "memory_leak"}, "values": [[end + 60, "0.9"]]}],     # outside: dropped
    })
    monkeypatch.setattr(app_module, "_prom_range", fake)
    d = client.get("/ui/history/episode", params={"machine": "web-01", "start": start, "end": end}, auth=CREDS).json()
    assert len(fake.calls) == 4
    for expr, lo, hi, step in fake.calls:
        assert 'machine="web-01"' in expr and "group_left" in expr and (lo, hi, step) == (start - 120, end + 120, 15)
    assert d["metrics"] == {"cpu": [[start - 15, 20.0], [start, 91.0], [end, 40.0]]}
    assert d["p_anomaly"][0] == [start, 0.97] and d["iso_score"] == [[start, 0.61]]
    assert d["causes"] == [{"cause": "cpu_spike", "seconds": 150}]
    assert [(r["reading_time"], r["label"]) for r in d["readings"]] == [("2026-06-29 00:00:00", 1), ("2026-06-29 01:00:00", 0)]


@pytest.mark.parametrize("params,code", [
    ({"machine": 'web-01"} or vector(1) #', "start": 1, "end": 2}, 422),     # no PromQL injection through the name
    ({"machine": "web-01", "start": 10, "end": 10}, 400),
    ({"machine": "web-01", "start": 0, "end": 9 * 86400}, 400),
    ({"machine": "web-01", "start": 1}, 422),
])
def test_episode_details_refuse_a_bad_machine_name_or_window(client, prom, params, code):
    assert client.get("/ui/history/episode", params=params, auth=CREDS).status_code == code
    assert prom.calls == []


def test_episode_details_need_the_credentials_and_report_prometheus_down(client, prom):
    q = {"machine": "web-01", "start": NOW - 60, "end": NOW - 30}
    assert client.get("/ui/history/episode", params=q).status_code == 401
    prom.error = app_module._PrometheusError("connection refused")
    assert client.get("/ui/history/episode", params=q, auth=CREDS).status_code == 502


def test_episode_details_are_reachable_from_the_tab():
    page = (ROOT / "api" / "static" / "control_panel.html").read_text()
    assert 'data-hsdetail="' in page and '"/ui/history/episode?"' in page and "function hsOpen(" in page


# ---- Details: every reading of the episode, from the cluster's prediction log ----

def _logged(pid, t, flagged, cpu):
    import datetime as dt
    return {"id": pid, "timestamp": dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(), "machine": "web-01",
            "final_is_anomaly": flagged, "final_cause": "cpu_spike" if flagged else "normal", "xgb_p_normal": 0.1,
            "predict_threshold": 0.91, "iso_score": 0.4, "model_version": "telecom_v4_rolling",
            "raw_metrics_json": '{"cpu": %s, "ram": 50}' % cpu, "features_json": "{}", "history_json": None}


def test_episode_details_list_every_reading_with_its_metrics_verdict_and_real_label(client, monkeypatch, labels):
    start, end = NOW - 600, NOW - 300
    calls = []

    def cluster(method, path, body=None, timeout=15):
        calls.append((method, path))
        return {"count": 3, "truncated": False, "predictions": [
            _logged("p1", start - 30, 0, 20), _logged("p2", start + 10, 1, 95), _logged("p3", start + 40, 1, 97)]}
    monkeypatch.setattr(app_module, "_cluster_call", cluster)
    monkeypatch.setattr(app_module, "_prom_range", lambda *a, **k: [])
    monkeypatch.setattr(labels.time, "time", lambda: start + 10)
    labels.record("p2", 1, "cpu_spike", "production agent", "2026-06-29 00:00:00", "web-01")
    labels.record("p3", 0, "normal", "production agent", "2026-06-29 00:00:30", "web-01")
    monkeypatch.setattr(app_module.time, "time", lambda: NOW)
    d = client.get("/ui/history/episode", params={"machine": "web-01", "start": start, "end": end}, auth=CREDS).json()
    (method, path), = calls
    q = dict(p.split("=") for p in path.split("?", 1)[1].split("&"))
    assert method == "GET" and path.startswith("/predictions/by-machine?") and q["machine"] == "web-01"
    assert (float(q["start"]), float(q["end"])) == (start - 60, end + 60)     # a minute before and after
    rows = {r["id"]: r for r in d["predictions"]}
    assert [r["id"] for r in d["predictions"]] == ["p1", "p2", "p3"] and d["predictions_error"] == ""
    assert rows["p2"]["is_anomaly"] == 1 and rows["p2"]["metrics"] == {"cpu": 95, "ram": 50}
    assert rows["p2"]["real"]["label"] == 1 and rows["p3"]["real"]["label"] == 0 and rows["p1"]["real"] is None


def test_episode_details_still_open_when_the_cluster_log_does_not_answer(client, monkeypatch, labels):
    monkeypatch.setattr(app_module, "_prom_range", lambda *a, **k: [])
    d = client.get("/ui/history/episode", params={"machine": "web-01", "start": NOW - 600, "end": NOW - 300},
                   auth=CREDS).json()
    assert d["predictions"] == [] and "no cluster" in d["predictions_error"] and "metrics" in d


def test_the_pods_answer_one_machines_readings_and_refuse_a_bad_window(monkeypatch):
    seen = []
    monkeypatch.setattr(app_module, "_db_ready", lambda: True)
    monkeypatch.setattr(app_module, "predictions_for_machine",
                        lambda m, s, e, limit=1000: seen.append((m, s, e, limit)) or ([{"id": "p1"}], True))
    c = TestClient(app_module.app)
    r = c.get("/predictions/by-machine", params={"machine": "web-01", "start": 100, "end": 200, "limit": 50})
    assert r.status_code == 200 and r.json() == {"count": 1, "truncated": True, "predictions": [{"id": "p1"}]}
    assert seen == [("web-01", 100.0, 200.0, 50)]
    assert c.get("/predictions/by-machine", params={"machine": "web-01", "start": 200, "end": 100}).status_code == 400
    assert c.get("/predictions/by-machine", params={"machine": "web 01;", "start": 1, "end": 2}).status_code == 422


def test_the_machine_window_query_compares_utc_iso_text_and_reports_truncation(monkeypatch):
    from api import feedback_db
    executed = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, args): executed.append((sql, args))
        def fetchall(self): return [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return Cur()
    monkeypatch.setattr(feedback_db, "_connect", lambda: Conn())
    rows, truncated = feedback_db.predictions_for_machine("web-01", 0, 60, limit=2)
    sql, args = executed[0]
    assert "machine = %s" in sql and "ORDER BY timestamp" in sql
    assert args == ("web-01", "1970-01-01T00:00:00+00:00", "1970-01-01T00:01:00+00:00", 3)
    assert [r["id"] for r in rows] == ["a", "b"] and truncated is True


def test_the_readings_table_is_in_the_details_window():
    page = (ROOT / "api" / "static" / "control_panel.html").read_text()
    assert "function hsReadings(" in page and 'hsReadings(ep,d),true)' in page

