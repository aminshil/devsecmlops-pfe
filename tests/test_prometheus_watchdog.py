"""The watchdog that reports Prometheus or Alertmanager down (scripts/prometheus_watchdog.py), with the
checks, Alertmanager and Telegram replaced by recorders: when it alerts, through which path, and that the
bot token never reaches a log line."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import prometheus_watchdog as wd  # noqa: E402


class Rig:
    def __init__(self, fail_post=False):
        self.up = {"prometheus": True, "alertmanager": True}
        self.posts, self.sent, self.events, self.fail_post = [], [], [], fail_post
        self.dog = wd.Watchdog(check=lambda t: self.up[t], post_alert=self.post, telegram=self.sent.append,
                               log=lambda *e: self.events.append(e), failures=2, refresh=30, alert_ttl=180)

    def post(self, alerts):
        if self.fail_post or not self.up["alertmanager"]:
            raise OSError("connection refused")
        self.posts.append(alerts[0])

    def run(self, t0, t1, step=5):
        for t in range(t0, t1, step):
            self.dog.step(float(t))


def firing(a, now):
    return wd.dt.datetime.fromisoformat(a["endsAt"]).timestamp() > now


def test_one_failed_check_is_not_an_outage():
    r = Rig()
    r.run(0, 10)
    r.up["prometheus"] = False
    r.dog.step(10.0)
    r.up["prometheus"] = True
    r.run(15, 60)
    assert r.posts == [] and r.sent == []


def test_prometheus_down_is_posted_to_alertmanager_on_the_second_failure_refreshed_then_resolved():
    r = Rig()
    r.run(0, 10)
    r.up["prometheus"] = False
    r.dog.step(10.0)
    assert r.posts == []
    r.dog.step(15.0)                                              # second failure: alert
    (a,) = r.posts
    assert a["labels"] == {"alertname": "PrometheusDown", "severity": "critical", "job": "prometheus-watchdog"}
    assert firing(a, 15.0) and "Prometheus is down" in a["annotations"]["summary"]
    r.run(20, 80)                                                 # refreshed every 30 s while it lasts
    assert len(r.posts) == 3 and all(firing(p, 60.0) for p in r.posts)
    r.up["prometheus"] = True
    r.dog.step(80.0)
    assert not firing(r.posts[-1], 80.0)                          # resolved: Alertmanager sends "resolved"
    assert r.sent == []                                           # Alertmanager was up: nothing direct
    r.run(85, 200)
    assert len(r.posts) == 4                                      # nothing more once resolved


def test_alertmanager_down_goes_straight_to_telegram_once_and_once_when_back():
    r = Rig()
    r.run(0, 10)
    r.up["alertmanager"] = False
    r.run(10, 60)
    assert len(r.sent) == 1 and "Alertmanager is down" in r.sent[0]
    r.up["alertmanager"] = True
    r.run(60, 100)
    assert len(r.sent) == 2 and "Alertmanager is back" in r.sent[1]
    assert r.posts == []


def test_both_down_is_one_message_straight_to_telegram_and_prometheus_alert_follows_when_alertmanager_is_back():
    r = Rig()
    r.run(0, 10)
    r.up["prometheus"] = r.up["alertmanager"] = False
    r.run(10, 40)
    assert r.sent == [r.sent[0]] and "Prometheus and Alertmanager are down" in r.sent[0]
    assert r.posts == []
    r.up["alertmanager"] = True
    r.dog.step(40.0)
    assert "Alertmanager is back" in r.sent[-1]
    assert len(r.posts) == 1 and firing(r.posts[0], 40.0)      # Prometheus still down: now delivered normally


def test_both_back_at_once_is_one_message():
    r = Rig()
    r.run(0, 10)
    r.up["prometheus"] = r.up["alertmanager"] = False
    r.run(10, 40)
    r.up["prometheus"] = r.up["alertmanager"] = True
    r.dog.step(40.0)
    assert len(r.sent) == 2 and "Prometheus and Alertmanager are back" in r.sent[1]


def test_prometheus_back_while_alertmanager_is_down_is_said_on_telegram():
    r = Rig()
    r.run(0, 10)
    r.up["alertmanager"] = False
    r.run(10, 25)
    r.up["prometheus"] = False
    r.run(25, 40)
    assert any("Prometheus is down" in m for m in r.sent)
    r.up["prometheus"] = True
    r.dog.step(40.0)
    assert "Prometheus is back" in r.sent[-1]


def test_an_alertmanager_refusal_is_logged_and_retried():
    r = Rig(fail_post=True)
    r.run(0, 10)
    r.up["prometheus"] = False
    r.run(10, 30)
    assert any(e[1] == "alertmanager refused the alert" for e in r.events)
    r.fail_post = False
    r.run(30, 40)
    assert len(r.posts) == 1


def test_the_token_never_reaches_a_log_line_or_an_error(tmp_path, capsys):
    token = "123456789:" + "A" * 35
    (tmp_path / "token").write_text(token)
    (tmp_path / "env").write_text("TELEGRAM_CHAT_ID=42\n")
    send = wd.telegram_sender(tmp_path / "token", tmp_path / "env", api="http://127.0.0.1:9")   # nothing listens
    with pytest.raises(RuntimeError) as e:
        send("hello")
    assert token not in str(e.value)
    log = wd.state_logger(tmp_path / "events.json")
    r = Rig()
    r.dog.telegram, r.dog.log = send, log
    r.up["alertmanager"] = False
    r.run(0, 20)
    out = capsys.readouterr().out + (tmp_path / "events.json").read_text()
    assert "Telegram send failed" in out and token not in out


def test_the_alert_takes_the_critical_route_to_telegram():
    am = yaml.safe_load((ROOT / "monitoring" / "alerting" / "alertmanager.yml.tmpl").read_text())
    labels = Rig().dog._alert(0.0, True)[0]["labels"]
    for route in am["route"]["routes"]:                                  # first matching route wins
        label, value = route["matchers"][0].split("=", 1)
        if labels.get(label) == value.strip('"'):
            assert route["receiver"] == "telegram" and route["group_wait"] == "1s"
            return
    pytest.fail("PrometheusDown matches no route")


def test_setup_installs_and_starts_the_service():
    setup = (ROOT / "scripts" / "setup_alerting.sh").read_text()
    assert "prometheus-watchdog.service" in setup and "scripts/prometheus_watchdog.py" in setup
    assert "systemctl enable --now prometheus-watchdog" in setup or "enable --now prometheus-watchdog" in setup
    json.loads(json.dumps(Rig().dog._alert(0.0, True)))                   # the posted payload is plain JSON
