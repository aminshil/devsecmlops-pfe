#!/usr/bin/env python3
"""
Stop test of the alerting chain itself, on the VM, as root: stops Prometheus, then Alertmanager, and times
how long the watchdog (scripts/prometheus_watchdog.py, service prometheus-watchdog) takes to report each.

  1. systemctl stop prometheus      -> PrometheusDown active in Alertmanager (Telegram follows within 1 s)
     systemctl start prometheus     -> PrometheusDown resolved (Alertmanager sends the resolved message)
  2. docker stop alertmanager       -> the watchdog sends "Alertmanager is down" straight to Telegram
     docker start alertmanager      -> it sends "Alertmanager is back"
Prometheus and Alertmanager are started again at the end, whatever happens. Results are printed and
written as JSON (--out), so the numbers quoted in the README can be traced to a file.

  sudo python3 scripts/watchdog_test.py
  sudo python3 scripts/watchdog_test.py --out models/results/watchdog_test_YYYYMMDD.json
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ok(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def am_firing(am, name):
    url = am + "/api/v2/alerts?" + urllib.parse.urlencode({"filter": f'alertname="{name}"'})
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            alerts = json.load(r)
    except OSError:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    return any(a["status"]["state"] == "active" and dt.datetime.fromisoformat(a["endsAt"].replace("Z", "+00:00")) > now
               for a in alerts)


def events_since(path, t):
    try:
        return [e for e in json.loads(Path(path).read_text()) if e["t"] >= t]
    except (OSError, ValueError):
        return []


def wait(what, test, t0, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if test():
            s = round(time.time() - t0, 1)
            print(f"  +{s:6.1f} s  {what}", flush=True)
            return s
        time.sleep(0.5)
    print(f"  (no '{what}' after {timeout:g} s)", flush=True)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--alertmanager", default="http://localhost:9093")
    ap.add_argument("--unit", default="prometheus", help="the Prometheus systemd unit")
    ap.add_argument("--container", default="alertmanager", help="the Alertmanager Docker container")
    ap.add_argument("--events", default="/run/prometheus-watchdog/events.json", help="the watchdog's event file")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "watchdog_test.json")
    args = ap.parse_args()
    prom, am = args.prometheus.rstrip("/"), args.alertmanager.rstrip("/")
    if not (ok(prom + "/-/healthy") and ok(am + "/-/healthy")):
        sys.exit("start with Prometheus and Alertmanager up")
    if not Path(args.events).exists():
        sys.exit(f"{args.events} missing: is the prometheus-watchdog service running?")
    run = lambda *c: subprocess.run(list(c), check=True, capture_output=True)   # noqa: E731
    report = {"started_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        print(time.strftime("%T"), "stopping Prometheus", flush=True)
        t0 = time.time(); run("systemctl", "stop", args.unit)
        down = wait("PrometheusDown active in Alertmanager", lambda: am_firing(am, "PrometheusDown"), t0, args.timeout)
        time.sleep(5)
        print(time.strftime("%T"), "starting Prometheus", flush=True)
        t1 = time.time(); run("systemctl", "start", args.unit)
        back = wait("PrometheusDown resolved", lambda: am_firing(am, "PrometheusDown") is False, t1, args.timeout)
        wait("Prometheus ready", lambda: ok(prom + "/-/ready"), t1, args.timeout)
        print("  (35 s for Alertmanager's resolved message: critical groups are flushed every 30 s)", flush=True)
        time.sleep(35)
        report["prometheus_s"] = {"stop_to_alert": down, "start_to_resolved": back}

        print(time.strftime("%T"), "stopping Alertmanager", flush=True)
        t2 = time.time(); run("docker", "stop", args.container)
        am_down = wait("'Alertmanager is down' sent straight to Telegram", lambda: any(
            e["target"] == "alertmanager" and e["event"] == "sent to Telegram directly" and "down" in e["detail"]
            for e in events_since(args.events, t2)), t2, args.timeout)
        time.sleep(5)
        print(time.strftime("%T"), "starting Alertmanager", flush=True)
        t3 = time.time(); run("docker", "start", args.container)
        am_back = wait("'Alertmanager is back' sent straight to Telegram", lambda: any(
            e["target"] == "alertmanager" and e["event"] == "sent to Telegram directly" and "back" in e["detail"]
            for e in events_since(args.events, t3)), t3, args.timeout)
        report["alertmanager_s"] = {"stop_to_telegram": am_down, "start_to_telegram": am_back}
    finally:
        subprocess.run(["systemctl", "start", args.unit], capture_output=True)
        subprocess.run(["docker", "start", args.container], capture_output=True)
    report["finished_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"written to {args.out}")
    return 0 if None not in (down, back, am_down, am_back) else 1


if __name__ == "__main__":
    sys.exit(main())
