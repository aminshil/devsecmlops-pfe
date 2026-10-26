#!/usr/bin/env python3
"""
End-to-end check of the Telegram anomaly list: the repository's rules and templates, run by a real
Prometheus and a real Alertmanager, with a stand-in serving pod and a stand-in Telegram server.
Nothing on the VM is touched (own ports, own temporary storage, no real Telegram, no token).

  stand-in pod :18090  ->  Prometheus :19090 (monitoring/alerting/alert_rules.yml, rules every 5 s)
                       ->  Alertmanager :19093 (alertmanager.yml.tmpl + telegram.tmpl)
                       ->  stand-in Telegram :18080 (records every message with its time)

Scenario (machines report every --cadence seconds, +-30%, like the production agent):
  start  the pod remembers each machine's last report from an hour ago (the agent was stopped), then the
         machines start reporting: they must be judged on their new reports at once
  m1 anomalous throughout, m4 anomalous until 40 s; m2, m3 normal
  40 s   m4 turns normal            -> a "back to normal" line
  60-110 s  nothing changes        -> no message (Prometheus re-sends its alerts, the clear expires)
  110 s  m1 stops reporting          -> listed as not reporting, last verdict anomalous
  170 s  every machine stops         -> "no information"
  230 s  every machine reports again -> "reporting" lines
Then, against Alertmanager alone: the largest list (200 anomalous, 60 back to normal, 60 not reporting,
60 reporting again) must stay under Telegram's 4,096 characters, and the panel's anomaly mute must
silence every alert of the list and nothing else.

Runs the pinned images with Docker (host network), or binaries given with --prometheus/--alertmanager.
Writes the messages, their delays and each check to --out (JSON). Exit code 1 if a check fails.

  python3 scripts/alerting_e2e.py
  python3 scripts/alerting_e2e.py --prometheus /path/prometheus --alertmanager /path/alertmanager
"""
import argparse
import datetime as dt
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALERTING = ROOT / "monitoring" / "alerting"
PROM_IMG, AM_IMG = "prom/prometheus:v2.53.2", "prom/alertmanager:v0.28.1"
POD, TG, PROM, AM = 18090, 18080, 19090, 19093
MACHINES = {"m1": "web", "m2": "db", "m3": "edge", "m4": "app"}

# ---- stand-ins -------------------------------------------------------------------------------------------
messages, stopped, verdict = [], set(), {"m1": 1, "m2": 0, "m3": 0, "m4": 1}
seen = {m: time.time() - 3600 for m in MACHINES}     # last report an hour ago, as after a stopped agent


class Telegram(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages.append({"t": time.time(), "text": body.get("text", "")})
        out = b'{"ok":true,"result":{"message_id":1,"date":0,"chat":{"id":1,"type":"private"}}}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class Pod(BaseHTTPRequestHandler):
    def do_GET(self):
        lines = []
        for m, mtype in MACHINES.items():
            lines.append(f'api_machine_last_seen_timestamp_seconds{{machine="{m}"}} {seen[m]}')
            lines.append(f'api_currently_anomalous{{machine="{m}",machine_type="{mtype}"}} {verdict[m]}')
        out = ("\n".join(lines) + "\n").encode()
        self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def agent(machine, cadence, stop):
    rng = random.Random(machine)
    while not stop.wait(rng.uniform(0.7 * cadence, 1.3 * cadence)):
        if machine not in stopped:
            seen[machine] = time.time()


def serve(handler, port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---- Prometheus and Alertmanager -------------------------------------------------------------------------
def configs(cfg: Path, in_docker: bool):
    base = "/cfg" if in_docker else str(cfg)
    shutil.copy(ALERTING / "alert_rules.yml", cfg / "alert_rules.yml")
    shutil.copy(ALERTING / "telegram.tmpl", cfg / "telegram.tmpl")
    (cfg / "telegram_token").write_text("000000:stand-in")
    (cfg / "prometheus.yml").write_text(
        "global: {scrape_interval: 5s, evaluation_interval: 5s}\n"
        f"rule_files: [{base}/alert_rules.yml]\n"
        f"alerting: {{alertmanagers: [{{static_configs: [{{targets: ['127.0.0.1:{AM}']}}]}}]}}\n"
        "scrape_configs:\n"
        "  - job_name: anomaly-api-pods\n"
        f"    static_configs: [{{targets: ['127.0.0.1:{POD}'], labels: {{pod: p1}}}}]\n")
    am = (ALERTING / "alertmanager.yml.tmpl").read_text()
    am = (am.replace("__TELEGRAM_CHAT_ID__", "1")
            .replace("/etc/alertmanager/telegram.tmpl", f"{base}/telegram.tmpl")
            .replace("/etc/alertmanager/telegram_token", f"{base}/telegram_token")
            .replace("        parse_mode: HTML\n", f"        parse_mode: HTML\n        api_url: http://127.0.0.1:{TG}\n"))
    assert am.count(f"api_url: http://127.0.0.1:{TG}") == 2, "both receivers must point at the stand-in Telegram"
    (cfg / "alertmanager.yml").write_text(am)
    for p in cfg.iterdir():
        p.chmod(0o644)
    cfg.chmod(0o755)


def start(args, cfg: Path):
    """Start Prometheus and Alertmanager; return a function that stops them."""
    prom_flags = ["--storage.tsdb.path=/tmp/e2e-prom" if not args.prometheus else f"--storage.tsdb.path={cfg}/pdata",
                  f"--web.listen-address=127.0.0.1:{PROM}"]
    am_flags = ["--storage.path=/tmp/e2e-am" if not args.alertmanager else f"--storage.path={cfg}/amdata",
                f"--web.listen-address=127.0.0.1:{AM}", "--cluster.listen-address="]
    procs, names = [], []
    for binary, img, name, conf, flags in ((args.alertmanager, AM_IMG, "e2e-alertmanager", "alertmanager.yml", am_flags),
                                           (args.prometheus, PROM_IMG, "e2e-prometheus", "prometheus.yml", prom_flags)):
        if binary:
            procs.append(subprocess.Popen([binary, f"--config.file={cfg}/{conf}", *flags],
                                          stdout=subprocess.DEVNULL, stderr=open(cfg / f"{name}.log", "w")))
        else:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "host", "-v", f"{cfg}:/cfg:ro",
                            img, f"--config.file=/cfg/{conf}", *flags], check=True, capture_output=True)
            names.append(name)

    def stop():
        for p in procs:
            p.terminate()
        for n in names:
            subprocess.run(["docker", "rm", "-f", n], capture_output=True)
    return stop


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw.strip() else {}


def wait_ready(url, secs=60):
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{url} not ready after {secs} s")


# ---- the scenario ----------------------------------------------------------------------------------------
def scenario(t0):
    events = []

    def at(sec, what, action):
        time.sleep(max(0.0, t0 + sec - time.time()))
        action()
        events.append({"t": round(time.time() - t0, 1), "event": what})
        print(f"+{time.time() - t0:5.1f}s {what}", flush=True)

    at(40, "m4 turns normal", lambda: verdict.update(m4=0))
    at(110, "m1 stops reporting (anomalous)", lambda: stopped.add("m1"))
    at(170, "every machine stops reporting", lambda: stopped.update(MACHINES))
    at(230, "every machine reports again", stopped.clear)
    at(260, "end", lambda: None)
    return events


def first_after(t0, sec, pattern, until):
    for m in messages:
        if t0 + sec <= m["t"] <= t0 + until and re.search(pattern, m["text"]):
            return round(m["t"] - t0 - sec, 1)
    return None


def telegram_length(text):
    return len(re.sub("<[^>]+>", "", text).encode("utf-16-le")) // 2     # Telegram counts UTF-16 units, tags removed


def largest_list():
    """Post the largest list straight to Alertmanager; return the length of the message it sends."""
    now = time.time()
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()

    def a(name, machine, started, ann=None):
        return {"labels": {"alertname": name, "severity": "info", "list": "anomalies", "machine": machine,
                           "machine_type": "firewall"},
                "annotations": ann or {}, "startsAt": iso(now - started), "endsAt": iso(now + 300)}
    alerts = [a("AnomalyDetected", f"firewall-{i:03d}", 600) for i in range(200)]
    alerts += [a("AnomalyCleared", f"firewall-c{i:02d}", 2) for i in range(60)]
    alerts += [a("MachineSilent", f"firewall-s{i:02d}", 3600, {"usual": "1m 0s", "last_verdict": "anomalous"}) for i in range(60)]
    alerts += [a("MachineReportingAgain", f"firewall-r{i:02d}", 2) for i in range(60)]
    n = len(messages)
    call("POST", f"http://127.0.0.1:{AM}/api/v2/alerts", alerts)
    time.sleep(15)
    sizes = [telegram_length(m["text"]) for m in messages[n:]]
    return max(sizes) if sizes else None


def mute_check():
    """The control panel's anomaly mute (api/app.py) silences the five alerts of the list, not the others."""
    src = (ROOT / "api" / "app.py").read_text()
    matchers = json.loads(re.search(r'"anomalies": \{"label": "the anomaly list",\s*"matchers": (\[.*?\])\}', src, re.S)
                          .group(1).replace("False", "false").replace("True", "true"))
    now = time.time()
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
    sid = call("POST", f"http://127.0.0.1:{AM}/api/v2/silences",
               {"matchers": matchers, "startsAt": iso(now), "endsAt": iso(now + 600), "createdBy": "e2e", "comment": "e2e"})["silenceID"]
    names = ["AnomalyDetected", "AnomalyCleared", "MachineSilent", "MachineReportingAgain", "FleetNoData"]
    alerts = [{"labels": {"alertname": n, "severity": "info", "list": "anomalies", "machine": "mute-test"},
               "startsAt": iso(now), "endsAt": iso(now + 300)} for n in names]
    alerts.append({"labels": {"alertname": "LayerDown", "severity": "critical", "layer": "mute-test"},
                   "startsAt": iso(now), "endsAt": iso(now + 300)})
    call("POST", f"http://127.0.0.1:{AM}/api/v2/alerts", alerts)
    time.sleep(3)
    state = {a["labels"]["alertname"]: a["status"]["state"] for a in call("GET", f"http://127.0.0.1:{AM}/api/v2/alerts")
             if a["labels"].get("machine") == "mute-test" or a["labels"].get("layer") == "mute-test"}
    call("DELETE", f"http://127.0.0.1:{AM}/api/v2/silence/{sid}")
    return state


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prometheus", help="Prometheus binary (default: the pinned image, with Docker)")
    ap.add_argument("--alertmanager", help="Alertmanager binary (default: the pinned image, with Docker)")
    ap.add_argument("--cadence", type=float, default=5.0, help="seconds between a machine's reports (default 5)")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "alerting_e2e.json")
    args = ap.parse_args()
    if bool(args.prometheus) != bool(args.alertmanager):
        sys.exit("give both --prometheus and --alertmanager, or neither (Docker)")
    import socket
    busy = [port for port in (POD, TG, PROM, AM) if socket.socket().connect_ex(("127.0.0.1", port)) == 0]
    if busy:
        sys.exit(f"port(s) {busy} already in use: stop what listens there (an earlier run?) and retry")
    cfg = Path(tempfile.mkdtemp(prefix="alerting-e2e-"))
    configs(cfg, in_docker=not args.prometheus)
    servers = [serve(Telegram, TG), serve(Pod, POD)]
    halt = threading.Event()
    for m in MACHINES:
        threading.Thread(target=agent, args=(m, args.cadence, halt), daemon=True).start()
    stop = start(args, cfg)
    try:
        wait_ready(f"http://127.0.0.1:{AM}/-/ready")
        wait_ready(f"http://127.0.0.1:{PROM}/-/ready")
        t0 = time.time()
        print(f"Prometheus :{PROM} and Alertmanager :{AM} up; machines report every {args.cadence:g} s (+-30%)", flush=True)
        events = scenario(t0)
        rules = call("GET", f"http://127.0.0.1:{PROM}/api/v1/rules")["data"]["groups"]
        rule_errors = [r["name"] for g in rules for r in g["rules"] if r.get("lastError")]
        limit = max(30.0, 3 * args.cadence)
        delays = {
            "back to normal": first_after(t0, 40, r"✅ − m4 \(app\) back to normal", 70),
            "m1 not reporting": first_after(t0, 110, r"⚫ m1 \(web\) · missed its reports.*last verdict anomalous", 170),
            "no information": first_after(t0, 170, r"no information", 230),
            "reporting again": first_after(t0, 230, r"🔌 m\d", 260),
        }
        quiet = [m for m in messages if t0 + 60 <= m["t"] < t0 + 110]
        size = largest_list()
        muted = mute_check()
    finally:
        stop()
        halt.set()
        for s in servers:
            s.shutdown()
    checks = {
        "every rule evaluates without error": not rule_errors,
        "back to normal within 15 s": delays["back to normal"] is not None and delays["back to normal"] <= 15,
        "no message in the 50 quiet seconds (re-sends, a clear expiring)": not quiet,
        f"a stopped machine listed within its limit ({limit:g} s) + 1.3 cadences + 15 s":
            delays["m1 not reporting"] is not None and delays["m1 not reporting"] <= limit + 1.3 * args.cadence + 15,
        f"no information within the limit + 1.3 cadences + 15 s once every machine stopped":
            delays["no information"] is not None and delays["no information"] <= limit + 1.3 * args.cadence + 15,
        "reporting again within 15 s": delays["reporting again"] is not None and delays["reporting again"] <= 15,
        "largest list under 4,096 characters": size is not None and size < 4096,
        "the anomaly mute silences the list and only the list":
            muted.get("LayerDown") == "active" and all(v == "suppressed" for k, v in muted.items() if k != "LayerDown")
            and len(muted) == 6,
    }
    def version(binary, img):
        if not binary:
            return img
        out = subprocess.run([binary, "--version"], capture_output=True, text=True)
        return ((out.stdout or out.stderr).splitlines() or ["?"])[0]      # e.g. "prometheus, version 2.53.2 (...)"
    report = {"finished_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "prometheus": version(args.prometheus, PROM_IMG), "alertmanager": version(args.alertmanager, AM_IMG),
              "cadence_s": args.cadence, "silence_limit_s": limit, "events": events, "delays_s": delays,
              "largest_list_chars": size, "mute_states": muted, "rule_errors": rule_errors,
              "messages": [{"t": round(m["t"] - t0, 1), "text": m["text"]} for m in messages if m["t"] < t0 + 270],
              "checks": checks}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    for m in report["messages"]:
        print(f"--- +{m['t']}s\n{m['text']}")
    print(json.dumps({"delays_s": delays, "largest_list_chars": size, "mute_states": muted}, indent=2, ensure_ascii=False))
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"written to {args.out}")
    shutil.rmtree(cfg, ignore_errors=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
