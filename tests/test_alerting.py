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
    alerts = {r["alert"] for r in _rules() if "alert" in r}
    for route in tmpl["route"]["routes"]:
        for m in route["matchers"]:
            assert m.split('"')[1] in alerts, m        # a route must not point at an alert that does not exist


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
