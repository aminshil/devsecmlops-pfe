"""Panel Stop/Fix for the host layers: systemd is the single authority. Found when Stop did nothing (pkill is
undone by Restart=always) and Fix started a second copy outside systemd."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENABLE_OPS_UI", "1")
os.environ.setdefault("OPS_UI_PASSWORD", "test-password-0123456789")

from fastapi.testclient import TestClient  # noqa: E402
import api.app as app_module  # noqa: E402

CREDS = ("fix-test-user", "fix-test-password-0123456789")
LAYERS = [("Prometheus", "prometheus"), ("K8s exporter", "k8s-exporter"), ("Production agent", "production-agent")]


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def forbidden(*a, **kw):
        raise AssertionError("the panel must not spawn or kill these processes itself")

    monkeypatch.setattr(app_module._sp, "run", fake_run)
    monkeypatch.setattr(app_module._sp, "Popen", forbidden)
    return seen


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "OPS_UI_ENABLED", True)
    monkeypatch.setattr(app_module, "_OPS_UI_USER", CREDS[0])
    monkeypatch.setattr(app_module, "_OPS_UI_PASSWORD", CREDS[1])
    return TestClient(app_module.app)


@pytest.mark.parametrize("layer,unit", LAYERS)
@pytest.mark.parametrize("action", ["stop", "start"])
def test_host_layers_use_systemctl(client, calls, layer, unit, action):
    r = client.post("/ui/fix", auth=CREDS, json={"layer": layer, "action": action})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert calls == [["systemctl", action, unit]]


def test_a_failing_systemctl_is_reported_not_hidden(client, monkeypatch):
    monkeypatch.setattr(app_module._sp, "run", lambda cmd, **kw: SimpleNamespace(returncode=5, stdout="", stderr="Unit not found."))
    r = client.post("/ui/fix", auth=CREDS, json={"layer": "Prometheus", "action": "stop"})
    assert r.json()["ok"] is False and "Unit not found" in r.json()["error"]


def test_the_agent_helpers_used_by_the_pipeline_control_use_systemctl_too(calls):
    assert app_module._stop_process(app_module._AGENT_SCRIPT, "Production agent")["ok"]
    assert app_module._start_process(app_module._AGENT_SCRIPT, "Production agent")["ok"]
    assert calls == [["systemctl", "stop", "production-agent"], ["systemctl", "start", "production-agent"]]


def test_an_unknown_process_keeps_the_old_behaviour(calls):
    app_module._stop_process("something_else.py", "Something")
    assert calls == [["pkill", "-f", "something_else.py"]]


def test_docker_layers_still_use_docker(client, calls):
    r = client.post("/ui/fix", auth=CREDS, json={"layer": "Grafana", "action": "stop"})
    assert r.json()["ok"] is True and calls == [["docker", "stop", "grafana"]]
