"""scripts/verify_mlflow_artifacts.py against a stand-in MLflow server: it downloads every file of the run
and compares its SHA-256 with the recorded one; a changed or missing file fails."""
import hashlib
import json
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {"telecom_xgb_classifier_v2.pkl": b"xgb-bytes", "telecom_baselines_v2.json": b'{"a": 1}'}


def serve(files):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/api/2.0/mlflow/artifacts/list":
                body = json.dumps({"files": [{"path": f"model/{n}", "is_dir": False} for n in files]}).encode()
            elif u.path == "/get-artifact" and q["path"][0].startswith("model/") and q["run_uuid"] == ["r1"]:
                body = files.get(q["path"][0][len("model/"):])
                if body is None:
                    self.send_response(404); self.end_headers(); return
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run(tmp_path, served, recorded):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"mlflow_run_id": "r1", "threshold_selection": {
        "artifacts": {f"models/{n}": hashlib.sha256(b).hexdigest() for n, b in recorded.items()}}}))
    srv = serve(served)
    try:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_mlflow_artifacts.py"),
                               "--report", str(report), "--out", str(tmp_path / "out.json"),
                               "--tracking-uri", f"http://127.0.0.1:{srv.server_port}"],
                              capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()


def test_identical_files_pass(tmp_path):
    r = run(tmp_path, FILES, FILES)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads((tmp_path / "out.json").read_text())
    assert out["all_match"] and [f["result"] for f in out["files"]] == ["match", "match"]


def test_a_changed_file_fails(tmp_path):
    r = run(tmp_path, {**FILES, "telecom_baselines_v2.json": b'{"a": 2}'}, FILES)
    assert r.returncode == 1 and "DIFFERENT" in r.stdout


def test_a_missing_file_fails(tmp_path):
    r = run(tmp_path, {"telecom_xgb_classifier_v2.pkl": FILES["telecom_xgb_classifier_v2.pkl"]}, FILES)
    assert r.returncode == 1 and "missing from the run" in r.stdout
