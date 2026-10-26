#!/usr/bin/env python3
"""
Stop test of the real traffic agent: how long the alerts take to notice that the machines stopped
reporting, and that they report again. Run on the VM, as root (it stops and starts the
production-agent service), with Prometheus on :9090.

  1. starts the agent if needed and waits until every machine has 3 new gaps between its reports in the
     last 5 minutes (so the usual intervals come from the current rules and current reports) and no
     machine is silent
  2. stops the agent and times, from the stop: the first MachineSilent, PredictionsStopped, FleetNoData
     (every machine silent), as Prometheus fires them (Telegram follows within about 5 s)
  3. starts the agent again and times: FleetNoData gone, the first MachineReportingAgain, no machine silent
The agent is running again at the end, whatever happens. Results are printed and written as JSON (--out),
so the numbers quoted in the README can be traced to a file.

  sudo python3 scripts/agent_stop_test.py
  sudo python3 scripts/agent_stop_test.py --out models/results/agent_stop_test_YYYYMMDD.json
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


def query(prom, expr):
    url = prom + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=10) as r:
        res = json.load(r)["data"]["result"]
    return float(res[0]["value"][1]) if res else 0.0


def firing(prom, alert):
    return query(prom, f'count(ALERTS{{alertname="{alert}",alertstate="firing"}}) or vector(0)')


def agent(action, unit):
    subprocess.run(["systemctl", action, unit], check=True)


def watch(prom, t0, conditions, timeout):
    """Poll every second; return {name: seconds after t0} for each condition once it holds."""
    seen = {}
    while time.time() - t0 < timeout and len(seen) < len(conditions):
        for name, test in conditions.items():
            if name not in seen and test():
                seen[name] = round(time.time() - t0, 1)
                print(f"  +{seen[name]:6.1f} s  {name}  ({int(firing(prom, 'MachineSilent'))} machines silent)", flush=True)
        time.sleep(1)
    return seen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--unit", default="production-agent")
    ap.add_argument("--machines", type=int, default=195, help="machines that must have 3 new gaps before the stop")
    ap.add_argument("--timeout", type=float, default=300, help="give up waiting for a step after this many seconds")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "agent_stop_test.json")
    args = ap.parse_args()
    prom = args.prometheus.rstrip("/")
    ready = f'count(count_over_time(fleet:machine_report_gap:seconds[5m]) >= 3) or vector(0)'
    report = {"started_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        agent("start", args.unit)
        print(time.strftime("%T"), f"agent running; waiting until {args.machines} machines have 3 new gaps", flush=True)
        end = time.time() + args.timeout
        while query(prom, ready) < args.machines or firing(prom, "MachineSilent") > 0:
            if time.time() > end:
                sys.exit(f"after {args.timeout:g} s: fewer than {args.machines} machines with 3 new gaps, or some still silent")
            time.sleep(5)
        report["before_stop"] = {
            "machines_with_interval": int(query(prom, "count(fleet:machine_report_interval:usual)")),
            "usual_interval_s": {q: round(query(prom, f"quantile({q}, fleet:machine_report_interval:usual)"), 1)
                                 for q in (0.0, 0.5, 1.0)},
            "silent": int(firing(prom, "MachineSilent")),
        }
        print(time.strftime("%T"), "before the stop:", json.dumps(report["before_stop"]), flush=True)

        t0 = time.time()
        agent("stop", args.unit)
        print(time.strftime("%T"), "agent STOPPED", flush=True)
        report["after_stop_s"] = watch(prom, t0, {
            "first MachineSilent": lambda: firing(prom, "MachineSilent") > 0,
            "PredictionsStopped": lambda: firing(prom, "PredictionsStopped") > 0,
            "FleetNoData (every machine silent)": lambda: firing(prom, "FleetNoData") > 0,
        }, args.timeout)

        t1 = time.time()
        agent("start", args.unit)
        print(time.strftime("%T"), "agent STARTED again", flush=True)
        report["after_restart_s"] = watch(prom, t1, {
            "FleetNoData gone": lambda: firing(prom, "FleetNoData") == 0,
            "first MachineReportingAgain": lambda: firing(prom, "MachineReportingAgain") > 0,
            "no machine silent": lambda: firing(prom, "MachineSilent") == 0,
        }, args.timeout)
    finally:
        agent("start", args.unit)     # leave the agent running
    report["finished_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"written to {args.out}")
    complete = len(report["after_stop_s"]) == 3 and len(report["after_restart_s"]) == 3
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
