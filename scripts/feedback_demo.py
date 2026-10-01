#!/usr/bin/env python3
"""
Feedback-loop demo: a one-page UI that walks through the whole loop.

  1. send readings to the LIVE API (v4 answers, each prediction is logged in PostgreSQL)
  2. judge them as an operator would (true/false positive, missed anomaly, ...)  -> POST /feedback/{id}
  3. retrain with those verdicts in a SCRATCH sandbox (tiny generated fleets, /tmp/feedback_demo)
  4. show the guardrail verdict and prove the production models were not touched

Run from the repo root, inside the venv:

    python3 scripts/feedback_demo.py                       # http://127.0.0.1:8765
    python3 scripts/feedback_demo.py --host 0.0.0.0        # to open it from another machine (lab network only)

What this proves: the MECHANICS (feedback rows enter training, the guardrail runs, production stays
untouched). It cannot prove the model improves: a tiny fleet and a dozen verdicts cannot show that.

Standard library only. It never passes the real models/ directory to any training command.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = Path("/tmp/feedback_demo")
API = "http://192.168.49.2:30080"
VERDICTS = ("true_positive", "false_positive", "true_negative", "false_negative")
CSV_COLS = ["id", "timestamp", "machine", "machine_type", "time_window", "raw_metrics_json",
            "history_json", "model_version", "final_is_anomaly", "final_cause",
            "operator_verdict", "verdict_timestamp"]

LOCK = threading.Lock()
STATE = {"rows": [], "retrain": {"status": "idle", "step": "", "log": [], "result": None}}


# ----------------------------------------------------------------------------- live API
def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API {method} {path} -> HTTP {e.code}: {e.read().decode()[:200]}") from e
    except OSError as e:
        raise RuntimeError(f"API {method} {path} unreachable at {API}: {e}") from e


# ----------------------------------------------------------------------------- scenarios
def scenario(name: str) -> dict:
    """A synthetic reading with its 10-reading history and a known ground truth."""
    r = random.Random()
    j = lambda x, s: round(x + r.uniform(-s, s), 2)          # noqa: E731
    machine, mtype = ("db-01", "db") if name == "disk_saturation" else ("web-01", "web")
    truth = 0 if name == "normal" else 1
    steady = lambda base, s: [j(base, s) for _ in range(10)]  # noqa: E731
    if name == "memory_leak":
        ram = [round(60 + i * 3.7, 2) for i in range(10)]
        metrics = {"cpu": j(45, 1), "ram": 95.0, "network": j(80, 4), "disk_io": j(25, 2),
                   "disk_usage": j(40, 1), "load_avg": 2.0}
        hist = {"cpu": steady(45, 1), "ram": ram, "load_avg": [round(1.5 + i * 0.055, 2) for i in range(10)]}
    elif name == "cpu_spike":
        metrics = {"cpu": 96.0, "ram": j(50, 2), "network": j(80, 4), "disk_io": j(25, 2),
                   "disk_usage": j(40, 1), "load_avg": 4.2}
        hist = {"cpu": steady(30, 2), "ram": steady(50, 2), "load_avg": steady(1.2, 0.1)}
    elif name == "disk_saturation":
        metrics = {"cpu": j(30, 2), "ram": j(50, 2), "network": j(80, 4), "disk_io": 85.0,
                   "disk_usage": 97.0, "load_avg": 4.5}
        hist = {"cpu": steady(30, 2), "ram": steady(50, 2), "load_avg": steady(1.2, 0.1)}
    else:  # normal
        metrics = {"cpu": j(30, 3), "ram": j(50, 2), "network": j(80, 5), "disk_io": j(25, 3),
                   "disk_usage": j(40, 1), "load_avg": j(1.2, 0.1)}
        hist = {"cpu": steady(30, 3), "ram": steady(50, 2), "load_avg": steady(1.2, 0.1)}
    return {"scenario": name, "truth": truth, "machine": machine, "type": mtype,
            "metrics": metrics, "history": hist}


SCENARIOS = ("normal", "memory_leak", "cpu_spike", "disk_saturation")
MIXED = ["normal", "normal", "normal", "memory_leak", "memory_leak", "cpu_spike",
         "cpu_spike", "disk_saturation", "disk_saturation", "normal", "memory_leak", "cpu_spike"]


def auto_verdict(row: dict) -> str:
    if row["is_anomaly"]:
        return "true_positive" if row["truth"] else "false_positive"
    return "false_negative" if row["truth"] else "true_negative"


def send(name: str) -> dict:
    sc = scenario(name)
    pred = api("POST", "/predict", {"machine": sc["machine"], "hour": 14,
                                    "metrics": sc["metrics"], "history": sc["history"]})
    pid = pred.get("prediction_id")
    if not pid:
        raise RuntimeError("the API returned no prediction_id: its feedback database is unreachable")
    row = {**sc, "id": pid, "ts": datetime.now(timezone.utc).isoformat(),
           "model": pred.get("model"), "is_anomaly": int(bool(pred.get("is_anomaly"))),
           "cause": pred.get("likely_cause") or "", "verdict": None, "verdict_ts": None}
    with LOCK:
        STATE["rows"].append(row)
    return row


def judge(pid: str, verdict: str) -> dict:
    with LOCK:
        row = next((r for r in STATE["rows"] if r["id"] == pid), None)
    if row is None:
        raise RuntimeError("unknown prediction id")
    allowed = ("true_positive", "false_positive") if row["is_anomaly"] else ("true_negative", "false_negative")
    if verdict not in allowed:
        raise RuntimeError(f"a prediction the model {'flagged' if row['is_anomaly'] else 'did not flag'} "
                           f"can be judged {' or '.join(allowed)}")
    resp = api("POST", f"/feedback/{pid}", {"verdict": verdict, "notes": "feedback_demo"})
    with LOCK:
        row["verdict"] = verdict
        row["verdict_ts"] = resp.get("verdict_timestamp") or datetime.now(timezone.utc).isoformat()
    return row


def write_feedback_csv(path: Path) -> int:
    """The same columns the retraining reads (see tests/test_train_production.py), built from what was
    sent and what the API answered. The real exporter, scripts/export_feedback_dataset.py, reads the
    PostgreSQL table directly, which the host cannot reach; this reproduces its CSV for the judged rows."""
    with LOCK:
        judged = [r for r in STATE["rows"] if r["verdict"]]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        w.writeheader()
        for r in judged:
            w.writerow({"id": r["id"], "timestamp": r["ts"], "machine": r["machine"],
                        "machine_type": r["type"], "time_window": "afternoon",
                        "raw_metrics_json": json.dumps(r["metrics"]),
                        "history_json": json.dumps(r["history"]),
                        "model_version": r["model"], "final_is_anomaly": r["is_anomaly"],
                        "final_cause": r["cause"] if r["verdict"] == "true_positive" else "",
                        "operator_verdict": r["verdict"], "verdict_timestamp": r["verdict_ts"]})
    return len(judged)


# ----------------------------------------------------------------------------- sandbox retraining
def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prod_fingerprint() -> dict:
    """Hashes of the manifest and every production artifact it lists."""
    manifest = ROOT / "models" / "manifest.json"
    names = list(((json.loads(manifest.read_text()).get("production") or {}).get("artifacts") or {}))
    return {p.name: sha256(p) for p in [ROOT / n for n in names] + [manifest] if p.exists()}


def _log(line: str) -> None:
    with LOCK:
        STATE["retrain"]["log"].append(line.rstrip())
        STATE["retrain"]["log"] = STATE["retrain"]["log"][-300:]


def _step(text: str) -> None:
    with LOCK:
        STATE["retrain"]["step"] = text
    _log(f"== {text}")


_CAP = None


def memory_cap_available() -> bool:
    """True only if systemd-run really works here (it exists on machines where systemd is not running)."""
    global _CAP
    if _CAP is None:
        try:
            _CAP = bool(shutil.which("systemd-run")) and subprocess.run(
                ["systemd-run", "--scope", "--quiet", "-p", "MemoryMax=4G", "true"],
                capture_output=True, timeout=15).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _CAP = False
        _log("memory cap: " + ("systemd-run MemoryMax=4G" if _CAP else "NOT available, running without a cap"))
    return _CAP


def run(cmd: list[str], ok=(0,)) -> int:
    env = {k: v for k, v in os.environ.items() if k != "MLFLOW_TRACKING_URI"}   # never log to MLflow
    env["PYTHONUNBUFFERED"] = "1"
    if memory_cap_available():        # a hard memory cap: a runaway job dies, the VM does not freeze
        cmd = ["systemd-run", "--scope", "--quiet", "-p", "MemoryMax=4G", "-p", "MemorySwapMax=0"] + cmd
    _log("$ " + " ".join(str(c) for c in cmd[-12:]))
    p = subprocess.Popen([str(c) for c in cmd], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        _log(line)
    rc = p.wait()
    if rc not in ok:
        raise RuntimeError(f"command failed with exit code {rc}: {' '.join(str(c) for c in cmd[-6:])}")
    return rc


def retrain_job() -> None:
    py, mlm = sys.executable, ROOT / "ml-model"
    data, models, w1, w2 = WORK / "data", WORK / "models", WORK / "work1", WORK / "work2"
    r1, r2, fb = WORK / "initial.json", WORK / "with_feedback.json", WORK / "feedback.csv"
    try:
        before = prod_fingerprint()
        data.mkdir(parents=True, exist_ok=True)
        fleets = {"train": 42, "test": 123, "val": 7}
        for name, seed in fleets.items():
            path = data / f"{name}.csv"
            if not path.exists():
                _step(f"generating a tiny {name} fleet (seed {seed})")
                run([py, mlm / "generate_telecom_fleet.py", "--machines", "30", "--days", "3", "--seed", seed,
                     "--anomaly-ratio", "0.08", "--output", path])
        for d in (models, w1, w2):
            shutil.rmtree(d, ignore_errors=True)
        models.mkdir(parents=True)   # train_production copies INTO models-dir and does not create it
        _step("1/3 train a stand-in 'production' model in the sandbox (never the real one)")
        run([py, mlm / "train_production.py", "--train", data / "train.csv", "--test", data / "test.csv",
             "--val", data / "val.csv", "--normal-sample", 5000, "--models-dir", models, "--work-dir", w1,
             "--threshold", 0.6, "--promote", "--report", r1])
        _step("2/3 write the operator verdicts as the training CSV")
        n = write_feedback_csv(fb)
        _log(f"{n} judged rows written to {fb}")
        _step("3/3 retrain WITH the feedback and compare with the stand-in production (no promotion)")
        run([py, mlm / "train_production.py", "--train", data / "train.csv", "--test", data / "test.csv",
             "--normal-sample", 5000, "--models-dir", models, "--work-dir", w2, "--feedback", fb,
             "--max-f1-drop", 1.0, "--max-recall-drop", 1.0, "--report", r2], ok=(0, 1))
        rep = json.loads(r2.read_text())
        after = prod_fingerprint()
        result = {
            "feedback_rows": n,
            "feedback_info": rep.get("feedback"),
            "candidate_v4_f1": ((rep.get("candidate_evaluation") or {}).get("v4") or {}).get("f1"),
            "stand_in_production_v4_f1": ((rep.get("production_evaluation") or {}).get("v4") or {}).get("f1"),
            "guardrail_passed": rep.get("guardrail_passed"),
            "guardrail_reasons": rep.get("guardrail_reasons"),
            "production_untouched": before == after and bool(before),
            "production_files_checked": len(before),
        }
        with LOCK:
            STATE["retrain"].update(status="done", step="finished", result=result)
    except Exception as e:                                   # noqa: BLE001 -- shown in the UI
        _log(f"ERROR: {e}")
        with LOCK:
            STATE["retrain"].update(status="error", step=str(e))


def start_retrain() -> None:
    with LOCK:
        if STATE["retrain"]["status"] == "running":
            raise RuntimeError("a retraining is already running")
        if not any(r["verdict"] for r in STATE["rows"]):
            raise RuntimeError("judge at least one prediction first")
        STATE["retrain"] = {"status": "running", "step": "starting", "log": [], "result": None}
    threading.Thread(target=retrain_job, daemon=True).start()


# ----------------------------------------------------------------------------- web UI
PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Feedback loop demo</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1d2433;background:#f6f7fb}
h1{margin:0 0 4px}.sub{color:#5b6579;margin-bottom:18px}
.card{background:#fff;border:1px solid #dfe3ee;border-radius:10px;padding:16px 18px;margin:14px 0}
.card h2{margin:0 0 8px;font-size:17px}.n{display:inline-block;background:#2b59ff;color:#fff;border-radius:50%;width:24px;height:24px;text-align:center;line-height:24px;margin-right:8px;font-size:13px}
button{font:inherit;border:0;border-radius:7px;padding:8px 14px;margin:3px 4px 3px 0;background:#2b59ff;color:#fff;cursor:pointer}
button.alt{background:#e8ecf8;color:#1d2433}button.good{background:#1f9d6b}button.bad{background:#d6455d}button:disabled{opacity:.45;cursor:default}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eef0f6}
.tag{padding:2px 8px;border-radius:99px;font-size:12px;font-weight:600}.t1{background:#ffe3e8;color:#b3263f}.t0{background:#dcf5ea;color:#16794f}
pre{background:#0f1626;color:#cfe3ff;padding:10px;border-radius:8px;max-height:260px;overflow:auto;font-size:12.5px}
.ok{color:#16794f;font-weight:700}.no{color:#b3263f;font-weight:700}.muted{color:#5b6579;font-size:13px}
</style>
<h1>Feedback loop demo</h1>
<div class="sub">Send readings to the live API, judge them like an operator, then retrain in a sandbox with your verdicts.</div>
<div class="card" id="status">checking the API...</div>

<div class="card"><h2><span class="n">1</span>Send readings (answered live by v4, logged in PostgreSQL)</h2>
<button onclick="send('mixed')">Send 12 mixed readings</button>
<button class="alt" onclick="send('normal')">+ normal</button><button class="alt" onclick="send('memory_leak')">+ memory leak</button>
<button class="alt" onclick="send('cpu_spike')">+ cpu spike</button><button class="alt" onclick="send('disk_saturation')">+ disk saturation</button></div>

<div class="card"><h2><span class="n">2</span>Judge them (this is what an operator does)</h2>
<div class="muted">Flagged by the model: was it a real anomaly or a false alarm? Not flagged: was it really normal, or did it miss one?</div>
<table><thead><tr><th>#</th><th>Scenario (truth)</th><th>Model said</th><th>Your verdict</th></tr></thead><tbody id="rows"></tbody></table>
<button class="good" onclick="post('/api/autojudge')">Auto-judge all with the scenario's ground truth</button></div>

<div class="card"><h2><span class="n">3</span>Retrain with the verdicts (sandbox, about 2 minutes)</h2>
<div class="muted">Tiny generated fleets in /tmp/feedback_demo, a stand-in model, no promotion, memory-capped. The real models/ is never passed to a command.</div>
<button id="rt" onclick="post('/api/retrain')">Retrain in the sandbox</button> <span id="step" class="muted"></span>
<pre id="log" style="display:none"></pre><div id="result"></div></div>

<div class="muted">What this proves: the mechanics (feedback rows enter training, the guardrail runs, production is untouched). It cannot show that the model improves: a tiny fleet and a dozen verdicts are far too few for that.</div>
<script>
const $=id=>document.getElementById(id);
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  const j=await r.json(); if(j.error) alert(j.error); refresh();}
async function send(s){await post('/api/send',{scenario:s});}
async function judge(id,v){await post('/api/judge',{id:id,verdict:v});}
function btn(r){ if(r.verdict) return '<b>'+r.verdict.replace('_',' ')+'</b>';
  const o=r.is_anomaly?[['true_positive','Real anomaly','good'],['false_positive','False alarm','bad']]:[['true_negative','Really normal','good'],['false_negative','Missed one','bad']];
  return o.map(x=>'<button class="'+x[2]+'" onclick="judge(\''+r.id+'\',\''+x[0]+'\')">'+x[1]+'</button>').join('');}
async function refresh(){
  const s=await (await fetch('/api/state')).json();
  $('status').innerHTML=s.api_ok?('API <span class="ok">reachable</span> at '+s.api+' &middot; logged predictions: <b>'+s.stats.total_predictions+'</b> &middot; judged: <b>'+JSON.stringify(s.stats.by_verdict)+'</b>'):('API <span class="no">unreachable</span>: '+s.api_error);
  $('rows').innerHTML=s.rows.map((r,i)=>'<tr><td>'+(i+1)+'</td><td>'+r.scenario+' <span class="tag t'+r.truth+'">'+(r.truth?'anomaly':'normal')+'</span></td><td>'+
    (r.is_anomaly?'flagged: '+(r.cause||'safety net'):'normal')+' <span class="muted">('+r.model+')</span></td><td>'+btn(r)+'</td></tr>').join('');
  const rt=s.retrain; $('rt').disabled=rt.status==='running'; $('step').textContent=rt.status==='running'?('running: '+rt.step):(rt.status==='error'?'error: '+rt.step:'');
  $('log').style.display=rt.log.length?'block':'none'; $('log').textContent=rt.log.join('\n'); $('log').scrollTop=1e9;
  const x=rt.result; $('result').innerHTML=!x?'':'<h3>Result</h3><table>'+
   '<tr><td>judged rows given to training</td><td><b>'+x.feedback_rows+'</b></td></tr>'+
   '<tr><td>how training used them</td><td><code>'+JSON.stringify(x.feedback_info)+'</code></td></tr>'+
   '<tr><td>candidate v4 F1 (with feedback) vs stand-in production</td><td><b>'+x.candidate_v4_f1+'</b> vs '+x.stand_in_production_v4_f1+'</td></tr>'+
   '<tr><td>guardrail</td><td class="'+(x.guardrail_passed?'ok':'no')+'">'+(x.guardrail_passed?'PASS':'REJECT')+' <span class="muted">'+(x.guardrail_reasons||[]).join('; ')+'</span></td></tr>'+
   '<tr><td>real production models untouched</td><td class="'+(x.production_untouched?'ok':'no')+'">'+(x.production_untouched?'YES':'NO')+' <span class="muted">('+x.production_files_checked+' files hashed before and after)</span></td></tr></table>';
}
refresh(); setInterval(refresh,1500);
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the terminal quiet
        pass

    def _send(self, obj, code=200, ctype="application/json"):
        body = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self._send(PAGE, ctype="text/html")
        if self.path == "/api/state":
            try:
                stats, ok, err = api("GET", "/feedback/stats"), True, ""
            except RuntimeError as e:
                stats, ok, err = {"total_predictions": "?", "by_verdict": {}}, False, str(e)
            with LOCK:
                return self._send({"api": API, "api_ok": ok, "api_error": err, "stats": stats,
                                   "rows": [{k: r[k] for k in ("id", "scenario", "truth", "is_anomaly", "cause",
                                                               "model", "verdict")} for r in STATE["rows"]],
                                   "retrain": dict(STATE["retrain"])})
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/send":
                names = MIXED if body.get("scenario") == "mixed" else [body.get("scenario")]
                if any(s not in SCENARIOS for s in names):
                    raise RuntimeError("unknown scenario")
                for s in names:
                    send(s)
                return self._send({"ok": True})
            if self.path == "/api/judge":
                judge(str(body.get("id")), str(body.get("verdict")))
                return self._send({"ok": True})
            if self.path == "/api/autojudge":
                with LOCK:
                    todo = [r for r in STATE["rows"] if not r["verdict"]]
                for r in todo:
                    judge(r["id"], auto_verdict(r))
                return self._send({"ok": True, "judged": len(todo)})
            if self.path == "/api/retrain":
                start_retrain()
                return self._send({"ok": True})
            self._send({"error": "not found"}, 404)
        except RuntimeError as e:
            self._send({"error": str(e)}, 400)


def main() -> int:
    global API
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=API, help="live API base URL (the Kubernetes NodePort)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    API = a.api.rstrip("/")
    if not (ROOT / "models" / "manifest.json").exists():
        print("run this from a repository checkout (models/manifest.json not found)", file=sys.stderr)
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"feedback demo: http://{a.host}:{a.port}   (live API: {API}, sandbox: {WORK})")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
