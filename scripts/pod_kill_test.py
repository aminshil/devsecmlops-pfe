#!/usr/bin/env python3
"""
Pod-kill test of the serving deployment: what happens to live traffic when an API pod dies.

While a steady stream of /predict requests goes through the NodePort (like any client), one
anomaly-api pod is deleted, first gracefully (the normal SIGTERM path, as in a rollout or a node
drain), then with a hard kill (--grace-period=0 --force, as in a crash). For each round it measures:
  - every request's result, so failed requests are counted and placed in time relative to the kill
  - the time until a replacement pod is Ready
  - the time until the deployment again has as many available replicas as desired
Standard library only, plus kubectl on PATH (run it on the VM). Results are printed and written
as JSON (--out), so the numbers quoted in the README can be traced to a file.

Usage:
  python3 scripts/pod_kill_test.py
  python3 scripts/pod_kill_test.py --modes graceful,force --rate 10 --out build/pod_kill.json
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BODY = json.dumps({"machine": "web-01", "hour": 14,
                   "metrics": {"cpu": 30, "ram": 50, "network": 80, "disk_io": 25, "disk_usage": 40, "load_avg": 1.2},
                   "history": {"cpu": [30.0] * 10, "ram": [50.0] * 10, "load_avg": [1.2] * 10}}).encode()


def kubectl(ns, *args):
    out = subprocess.run(["kubectl", "-n", ns, *args], capture_output=True, text=True, timeout=30, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def pods(ns, selector):
    """name -> {"ready": bool, "terminating": bool} for the deployment's pods."""
    items = json.loads(kubectl(ns, "get", "pods", "-l", selector, "-o", "json"))["items"]
    out = {}
    for p in items:
        conds = {c["type"]: c["status"] for c in (p.get("status") or {}).get("conditions") or []}
        out[p["metadata"]["name"]] = {"ready": conds.get("Ready") == "True",
                                      "terminating": bool(p["metadata"].get("deletionTimestamp"))}
    return out


def deployment(ns, name):
    d = json.loads(kubectl(ns, "get", "deployment", name, "-o", "json"))
    return d["spec"].get("replicas", 1), (d.get("status") or {}).get("availableReplicas", 0)


class Load:
    """`workers` threads each sending /predict at rate/workers per second; every result is kept."""
    def __init__(self, url, rate, workers=4):
        self.url, self.interval, self.workers = url.rstrip("/") + "/predict", workers / rate, workers
        self.results, self.lock, self.stop = [], threading.Lock(), threading.Event()

    def _one(self):
        t = time.time()
        req = urllib.request.Request(self.url, data=BODY, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        except (urllib.error.URLError, OSError) as e:
            status = type(e).__name__
        return t, time.time() - t, status

    def _run(self, offset):
        time.sleep(offset)
        while not self.stop.is_set():
            res = self._one()
            with self.lock:
                self.results.append(res)
            self.stop.wait(max(0.0, self.interval - res[1]))

    def start(self):
        self.threads = [threading.Thread(target=self._run, args=(i * self.interval / self.workers,), daemon=True)
                        for i in range(self.workers)]
        for th in self.threads:
            th.start()

    def finish(self):
        self.stop.set()
        for th in self.threads:
            th.join(timeout=15)
        return list(self.results)


def percentile(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def summarise(results, t0, t_new_ready, t_available):
    """Requests from 5 s before the kill until the end of the round, relative to the kill (t0)."""
    window = [r for r in results if r[0] >= t0 - 5]
    failed = [r for r in window if r[2] != 200]
    during = [r[1] for r in window if r[0] <= (t_available or t0)]
    return {
        "requests": len(window),
        "failed": len(failed),
        "failed_at_s": [round(r[0] - t0, 2) for r in failed][:50],
        "failure_kinds": sorted({str(r[2]) for r in failed}),
        "new_pod_ready_after_s": round(t_new_ready - t0, 1) if t_new_ready else None,
        "available_restored_after_s": round(t_available - t0, 1) if t_available else None,
        "latency_ms_until_restored": {"p50": round(1000 * percentile(during, 0.5), 1) if during else None,
                                      "p95": round(1000 * percentile(during, 0.95), 1) if during else None,
                                      "max": round(1000 * max(during), 1) if during else None},
    }


def round_(args, load, mode):
    desired, available = deployment(args.namespace, args.deployment)
    before = pods(args.namespace, args.selector)
    ready = sorted(n for n, p in before.items() if p["ready"] and not p["terminating"])
    if available < desired or len(ready) < desired:
        raise RuntimeError(f"not starting {mode}: {available}/{desired} available, ready pods {ready}")
    victim = ready[0]
    time.sleep(args.settle)
    extra = ["--grace-period=0", "--force"] if mode == "force" else []
    t0 = time.time()
    kubectl(args.namespace, "delete", "pod", victim, "--wait=false", *extra)
    print(f"[{mode}] t0: deleted {victim} ({desired} desired, ready before: {', '.join(ready)})", flush=True)
    t_new_ready = t_available = None
    while time.time() - t0 < args.timeout and t_available is None:
        now = pods(args.namespace, args.selector)
        new_ready = [n for n, p in now.items() if n not in before and p["ready"] and not p["terminating"]]
        if new_ready and t_new_ready is None:
            t_new_ready = time.time()
            print(f"[{mode}] +{t_new_ready - t0:.1f}s replacement {new_ready[0]} Ready", flush=True)
        live_ready = [n for n, p in now.items() if p["ready"] and not p["terminating"]]
        if t_new_ready and len(live_ready) >= desired and deployment(args.namespace, args.deployment)[1] >= desired:
            t_available = time.time()
            print(f"[{mode}] +{t_available - t0:.1f}s {desired}/{desired} available again", flush=True)
        time.sleep(0.5)
    time.sleep(args.settle)
    out = {"mode": mode, "victim": victim, "desired_replicas": desired,
           **summarise(load.results[:], t0, t_new_ready, t_available)}
    print(f"[{mode}] {out['failed']} failed of {out['requests']} requests"
          + (f" (at {out['failed_at_s']} s, {out['failure_kinds']})" if out["failed"] else ""), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://192.168.49.2:30080")
    ap.add_argument("--namespace", default="ml-serving")
    ap.add_argument("--deployment", default="anomaly-api")
    ap.add_argument("--selector", default="app=anomaly-api")
    ap.add_argument("--modes", default="graceful,force")
    ap.add_argument("--rate", type=float, default=10.0, help="requests per second during the test")
    ap.add_argument("--settle", type=float, default=15.0, help="seconds of steady load before and after each kill")
    ap.add_argument("--timeout", type=float, default=240.0, help="give up waiting for recovery after this many seconds")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "pod_kill.json")
    args = ap.parse_args()
    image = kubectl(args.namespace, "get", "deployment", args.deployment,
                    "-o", "jsonpath={.spec.template.spec.containers[0].image}").strip()
    print(f"{args.deployment} ({image}) via {args.url}, {args.rate:g} req/s; rounds: {args.modes}", flush=True)
    load = Load(args.url, args.rate)
    load.start()
    rounds = []
    try:
        for mode in args.modes.split(","):
            rounds.append(round_(args, load, mode.strip()))
    finally:
        results = load.finish()
    report = {"image": image, "url": args.url, "rate_per_s": args.rate, "rounds": rounds,
              "total_requests": len(results), "total_failed": sum(1 for r in results if r[2] != 200),
              "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    print(json.dumps(report, indent=2))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"written to {args.out}")
    return 0 if all(r["available_restored_after_s"] is not None for r in rounds) else 1


if __name__ == "__main__":
    sys.exit(main())
