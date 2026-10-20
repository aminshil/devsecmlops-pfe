"""scripts/ci_config.py against a fake Jenkins + SonarQube (a local HTTP server), so the request
paths, the CSRF crumb, the secret blanking and the file layout are checked without the real servers."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ci_config as cc  # noqa: E402

JOB_XML = ("<flow-definition><description>x</description>"
           "<credentials><password>hunter2</password><secret>abc</secret></credentials></flow-definition>")


@pytest.fixture()
def fake():
    log = []

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            log.append(("GET", self.path, self.headers.get("Authorization")))
            if self.path == "/job/devsecmlops-pipeline/config.xml":
                return self._send(200, JOB_XML.encode(), "application/xml")
            if self.path == "/job/devsecmlops-model/config.xml":
                return self._send(404, b"no")
            if self.path.startswith("/pluginManager/api/json"):
                return self._send(200, {"plugins": [{"shortName": "git", "version": "5.0", "enabled": True},
                                                    {"shortName": "old", "version": "1", "enabled": False}]})
            if self.path == "/crumbIssuer/api/json":
                return self._send(200, {"crumbRequestField": "Jenkins-Crumb", "crumb": "CR"})
            if self.path.startswith("/api/webhooks/list"):
                return self._send(200, {"webhooks": [{"name": "jenkins", "url": "http://jenkins:8080/sonarqube-webhook/"}]})
            if self.path.startswith("/api/"):
                return self._send(200, {"ok": self.path})
            self._send(404, b"nf")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            log.append(("POST", self.path, self.headers.get("Jenkins-Crumb"), self.rfile.read(n)))
            self._send(200, b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", log
    srv.shutdown()


def _env(monkeypatch):
    monkeypatch.setenv("JENKINS_USER", "admin"); monkeypatch.setenv("JENKINS_TOKEN", "t")
    monkeypatch.setenv("SONAR_TOKEN", "s")


def test_export_writes_files_and_blanks_secrets(fake, tmp_path, monkeypatch):
    url, log = fake
    _env(monkeypatch)
    assert cc.main(["export", "--jenkins", url, "--sonar", url, "--out", str(tmp_path)]) == 0
    xml = (tmp_path / "jenkins/jobs/devsecmlops-pipeline.xml").read_text()
    assert "hunter2" not in xml and "abc" not in xml and "<password></password>" in xml
    assert not (tmp_path / "jenkins/jobs/devsecmlops-model.xml").exists()          # 404 is skipped, not fatal
    assert (tmp_path / "jenkins/plugins.txt").read_text() == "git:5.0\n"           # disabled plugin left out
    assert json.loads((tmp_path / "sonarqube/webhooks.json").read_text())["webhooks"][0]["name"] == "jenkins"
    assert all(m == "GET" for m, *_ in log)                                        # export never writes
    assert all(a and a.startswith("Basic ") for _, _, a, *rest in log if True)


def test_restore_needs_yes_and_uses_the_crumb(fake, tmp_path, monkeypatch):
    url, log = fake
    _env(monkeypatch)
    (tmp_path / "jenkins/jobs").mkdir(parents=True)
    (tmp_path / "jenkins/jobs/devsecmlops-model.xml").write_text("<flow-definition/>")
    (tmp_path / "sonarqube").mkdir()
    (tmp_path / "sonarqube/webhooks.json").write_text(json.dumps(
        {"webhooks": [{"name": "jenkins", "url": "http://jenkins:8080/sonarqube-webhook/"}]}))
    assert cc.main(["restore", "--jenkins", url, "--sonar", url, "--out", str(tmp_path)]) == 2
    assert not [e for e in log if e[0] == "POST"]
    assert cc.main(["restore", "--yes", "--jenkins", url, "--sonar", url, "--out", str(tmp_path)]) == 0
    posts = [e for e in log if e[0] == "POST"]
    assert any(p[1] == "/createItem?name=devsecmlops-model" and p[2] == "CR" for p in posts)
    assert any(p[1].startswith("/api/projects/create") for p in posts)
    assert any(p[1].startswith("/api/webhooks/create") for p in posts)


def test_missing_credentials_is_a_clear_error(monkeypatch):
    for v in ("JENKINS_USER", "JENKINS_TOKEN", "SONAR_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    assert cc.main(["export"]) == 2
