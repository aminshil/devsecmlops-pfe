"""The alerting configuration (monitoring/alerting/), checked without Prometheus: the files parse and
are wired together, every metric a rule uses is one this project exports, every layer the Status tab
checks is also watched by an alert, and no Telegram secret is in the repository. The rules' behaviour
is unit-tested by promtool (alert_rules_test.yml, run by scripts/check_alerting.sh)."""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ALERTING = ROOT / "monitoring" / "alerting"
RULES = yaml.safe_load((ALERTING / "alert_rules.yml").read_text())
PROM = yaml.safe_load((ROOT / "monitoring" / "prometheus.yml").read_text())


def _rules():
    return [r for g in RULES["groups"] for r in g["rules"]]


def test_prometheus_loads_the_rules_and_sends_to_alertmanager():
    assert PROM["rule_files"] == ["alerting/alert_rules.yml"]
    assert (ROOT / "monitoring" / PROM["rule_files"][0]).exists()
    targets = [t for am in PROM["alerting"]["alertmanagers"] for sc in am["static_configs"] for t in sc["targets"]]
    assert targets == ["localhost:9093"]
    tmpl = yaml.safe_load((ALERTING / "alertmanager.yml.tmpl").read_text().replace("__TELEGRAM_CHAT_ID__", "1"))
    names = {r["name"] for r in tmpl["receivers"]}
    assert tmpl["route"]["receiver"] in names and all(rt["receiver"] in names for rt in tmpl["route"]["routes"])
    known = {"alertname": {r["alert"] for r in _rules() if "alert" in r},
             "severity": {r["labels"]["severity"] for r in _rules() if "alert" in r}}
    for route in tmpl["route"]["routes"]:
        for m in route["matchers"]:
            label, value = m.split("=", 1)[0], m.split('"')[1]
            assert value in known[label], m   # a route must not point at an alert or severity that does not exist


def test_every_metric_in_the_rules_is_exported_by_this_project():
    app = (ROOT / "api" / "app.py").read_text()
    exporter = (ROOT / "monitoring" / "k8s_exporter.py").read_text()
    builtin = {"up", "probe_success", "node_filesystem_avail_bytes", "node_filesystem_size_bytes",
               "node_memory_MemAvailable_bytes"}
    recorded = {r["record"] for r in _rules() if "record" in r}
    words = set()
    for r in _rules():
        expr = re.sub(r'"[^"]*"', '""', r["expr"])          # label values are not metric names
        words |= set(re.findall(r"\b((?:api|k8s|node|probe)_[A-Za-z0-9_]+)\b", expr))
    assert words, "no metric found: the extraction is broken"
    for name in sorted(words - recorded - builtin):
        assert f'"{name}"' in app or f'"{name}"' in exporter, f"{name} is not exported by api/app.py or k8s_exporter.py"


def test_every_layer_on_the_status_tab_is_watched_by_an_alert():
    import api.app as app_module
    probed = {sc["labels"]["layer"] for job in PROM["scrape_configs"] if job["job_name"] == "blackbox-layers"
              for sc in job["static_configs"]}
    scraped = {job["job_name"] for job in PROM["scrape_configs"]}
    watched_by = {  # Status-tab layer -> how an alert sees it going down
        "Docker registry": "Docker registry" in probed, "Jenkins": "Jenkins" in probed,
        "SonarQube": "SonarQube" in probed, "MLflow": "MLflow" in probed, "Grafana": "Grafana" in probed,
        "Node exporter": "node-exporter" in scraped, "K8s exporter": "k8s-exporter" in scraped,
        "Prometheus": True,   # cannot alert on itself; documented in the README (alerting limits)
    }
    for _, _, _, name, _ in app_module._PORT_LAYERS:
        assert watched_by.get(name), f"Status-tab layer {name!r} has no alert"
    alerts = {r["alert"] for r in _rules() if "alert" in r}
    assert {"KubernetesUnreachable", "PredictionsStopped", "ScrapeTargetDown"} <= alerts  # Kubernetes, agent, panel


def test_no_telegram_secret_in_the_repository():
    tmpl = (ALERTING / "alertmanager.yml.tmpl").read_text()
    assert "bot_token_file:" in tmpl and "__TELEGRAM_CHAT_ID__" in tmpl
    assert not re.search(r"^\s*bot_token\s*:", tmpl, re.M)
    token_shape = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
    for p in list(ALERTING.iterdir()) + [ROOT / "scripts" / "setup_alerting.sh", ROOT / "monitoring" / "prometheus.yml"]:
        assert not token_shape.search(p.read_text()), f"something shaped like a bot token in {p.name}"


def test_the_grafana_anomaly_panels_are_the_ones_unit_tested_against_the_telegram_list():
    import json
    dash = json.loads((ROOT / "monitoring" / "grafana" / "dashboards" / "devsecmlops-fleet.json").read_text())
    panels = {p.get("title"): p for p in dash["panels"]}
    tested = {t["expr"] for case in yaml.safe_load((ALERTING / "alert_rules_test.yml").read_text())["tests"]
              for t in case.get("promql_expr_test", [])}
    for title in ("Anomalous machines now (the Telegram list)", "Anomalous machines over time"):
        expr = panels[title]["targets"][0]["expr"].replace("$machine_type", ".*")
        assert "fleet:machine_anomalous:fresh" in expr, title          # same state as the alerts
        assert expr in tested, f"{title}: the dashboard query is not the one alert_rules_test.yml checks"


def test_every_per_machine_dashboard_panel_counts_only_machines_that_still_report():
    import json
    dash = json.loads((ROOT / "monitoring" / "grafana" / "dashboards" / "devsecmlops-fleet.json").read_text())
    tested = {t["expr"] for case in yaml.safe_load((ALERTING / "alert_rules_test.yml").read_text())["tests"]
              for t in case.get("promql_expr_test", [])}
    per_machine = re.compile(r"\b(api_input_metric|api_classifier_p_anomaly|api_anomaly_cause_p|api_currently_anomalous)\b")
    checked = 0
    for p in dash["panels"]:
        for t in p.get("targets", []):
            expr = t.get("expr", "")
            if per_machine.search(expr) or "fleet:machine_anomalous:fresh" in expr:
                checked += 1
                fresh = "fleet:machine_anomalous:fresh" in expr or expr.count("< 300") >= expr.count("group_left()")
                assert fresh, f"{p['title']}: reads per-machine gauges without the 5-minute freshness rule"
    assert checked >= 6
    panels = {p.get("title"): p for p in dash["panels"]}
    for title in ("Fleet-wide average CPU", "Classifier anomaly probability by machine (top 25)", "Alerts firing now"):
        assert panels[title]["targets"][0]["expr"].replace("$machine_type", ".*") in tested, title
    probes = panels["Platform layers (HTTP probe every 5 s)"]["targets"][0]["expr"]
    rule = next(r for g in RULES["groups"] for r in g["rules"] if r.get("alert") == "LayerDown")["expr"]
    assert probes in rule      # the panel shows the series the LayerDown alert reads
