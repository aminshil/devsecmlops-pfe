#!/usr/bin/env python3
"""
production_agent.py -- the ONLY synthetic part of this pipeline is the data
source (real historical readings from the held-out test set, standing in
for a real monitoring agent on a real machine). Everything downstream is
genuine, unmodified production behavior:

    this script  -->  POST /predict  -->  the API decides + serves the
    result  -->  the API's OWN self-instrumentation (api_predictions_total,
    api_currently_anomalous, api_anomaly_score, api_input_metric, etc., see
    api/app.py) reports it  -->  Prometheus scrapes the API directly  -->
    Grafana.

Also periodically relays currently-anomalous machines to /root-cause, so
the "Root cause spotlight" / "Root cause events" panels have data too --
but the relaying is the ONLY thing this script does for that: it reads
"who is currently anomalous, per the API's own report" straight from
Prometheus (api_currently_anomalous{...}==1), and asks /root-cause to rank
them. No scoring, no causality logic, no computation happens here -- the
API answers both questions (is this anomalous? / who's the real cause?)
entirely on its own; this script only decides WHEN to ask.

Deliberately does NOT re-publish results itself (unlike monitoring/
anomaly_bridge.py, a separate, richer demo layer kept as-is, untouched).
This script's only job is to look, from the API's point of view, exactly
like a real caller.

Reads data/telecom_fleet_v2_test.csv -- the INDEPENDENT, held-out test
set (never trained on), not the training data monitoring/replay_exporter.py
replays. This is the more honest "production realism" choice: it proves
the model genuinely generalizes to unseen readings, not that it recognizes
memorized training rows.

Sampling deliberately mirrors api/app.py's own _load_sample_pool /
_collect_samples: this file can be very large (a multi-day, 30s-resolution,
200-machine simulation), so this seeks to random BYTE OFFSETS across the
file rather than reading it in full -- loading the whole thing into memory
first (an earlier version of this script did exactly that) made even
starting up take an unreasonably long time on the real dataset.

Usage:
    python3 monitoring/production_agent.py                # -> K8s NodePort
    python3 monitoring/production_agent.py --target host   # -> host API :8000
    python3 monitoring/production_agent.py --interval 5    # seconds between /predict calls
    python3 monitoring/production_agent.py --no-root-cause # skip the /root-cause relay
"""
import argparse
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "telecom_fleet_v2_test.csv"
FEATURES = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]
N_SEEKS = 800  # matches api/app.py's own proven sample size for this file
PROMETHEUS_URL = "http://localhost:9090"
ROOT_CAUSE_INTERVAL = 20  # seconds between /root-cause relays (independent of --interval)

TARGETS = {
    # Minikube's Docker driver only forwards a small fixed set of ports to
    # localhost -- the NodePort is NOT one of them (confirmed live: the
    # node's own IP is required, see the README "operational incident"
    # entry this discovery led to). 192.168.49.2 is Minikube's own fixed
    # convention, stable across rebuilds -- not a dynamically assigned IP.
    "k8s":  "http://192.168.49.2:30080",
    "host": "http://localhost:8000",
}


def build_sample_pool(rng):
    """Seek to N_SEEKS positions across the file; at each, read one row.
    Same technique as api/app.py's _collect_samples -- never reads the
    file in full."""
    print(f"Indexing {DATA_PATH} (seek-sampling, not a full read) ...")
    with open(DATA_PATH, "r") as f:
        header = f.readline().strip().split(",")
        size = os.path.getsize(DATA_PATH)
        pool = []
        for k in range(N_SEEKS):
            f.seek(int(size * k / N_SEEKS))
            f.readline()  # discard the partial line we landed inside
            line = f.readline().strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != len(header):
                continue
            pool.append(dict(zip(header, parts)))
    rng.shuffle(pool)
    print(f"Sampled {len(pool)} rows across the file (from {N_SEEKS} seek points)")
    return pool


def row_to_request(row):
    # Real hour from the row's own timestamp, never hardcoded -- hardcoding
    # a fixed hour here would score every reading against the wrong
    # per-time-window baseline (the same "3 AM problem" mistake documented
    # elsewhere in this project).
    ts = row.get("timestamp", "")
    try:
        hour = int(ts[11:13])
    except (ValueError, IndexError):
        hour = 14  # last-resort fallback only, never the normal path
    return {
        "machine": row["machine"],
        "machine_type": row.get("type") or None,
        "hour": hour,
        "metrics": {c: float(row[c]) for c in FEATURES},
    }


def send_one(base_url, row):
    body = json.dumps(row_to_request(row)).encode()
    req = urllib.request.Request(
        base_url + "/predict", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print(f"  {result.get('machine','?'):10} is_anomaly={result.get('is_anomaly')} "
                  f"cause={result.get('likely_cause')}")
    except urllib.error.URLError as e:
        print(f"  request failed: {e}")


def prometheus_query(promql):
    """Read-only lookup against Prometheus -- this is how the script learns
    'who is currently anomalous, per the API's own report', without itself
    tracking or computing anything."""
    url = PROMETHEUS_URL + "/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
            return data.get("data", {}).get("result", [])
    except (urllib.error.URLError, json.JSONDecodeError):
        return []


def relay_root_cause(base_url):
    """Read currently-anomalous machines + their scores straight from
    Prometheus (both self-reported by the API via /predict), and ask
    /root-cause to rank them. All ranking logic is server-side; this
    function only decides WHEN to ask and forwards what the API itself
    already said."""
    anomalous = prometheus_query('api_currently_anomalous == 1')
    if not anomalous:
        return

    machines = [r["metric"]["machine"] for r in anomalous if "machine" in r["metric"]]
    scores = prometheus_query('api_anomaly_score')
    score_by_machine = {
        r["metric"]["machine"]: float(r["value"][1])
        for r in scores if "machine" in r["metric"]
    }

    batch = {m: score_by_machine.get(m, 1.0) for m in machines}
    if not batch:
        return

    body = json.dumps({"anomalies": batch}).encode()
    req = urllib.request.Request(
        base_url + "/root-cause", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            causes = result.get("likely_root_causes", [])
            if causes:
                print(f"  [root-cause] likely root cause(s): {', '.join(causes)}")
    except urllib.error.URLError as e:
        print(f"  [root-cause] relay failed: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", choices=list(TARGETS), default="k8s",
                     help="which serving path to hit (default: k8s, the NodePort)")
    ap.add_argument("--interval", type=float, default=5.0,
                     help="seconds between /predict requests (default: 5)")
    ap.add_argument("--no-root-cause", action="store_true",
                     help="skip the periodic /root-cause relay")
    args = ap.parse_args()

    base_url = TARGETS[args.target]
    rng = random.SystemRandom()
    pool = build_sample_pool(rng)
    if not pool:
        print("no rows sampled -- check DATA_PATH / file contents")
        return

    print(f"Sending real test-set readings to {base_url}/predict every {args.interval}s")
    if not args.no_root_cause:
        print(f"Relaying currently-anomalous machines to /root-cause every "
              f"~{ROOT_CAUSE_INTERVAL}s (reading state FROM Prometheus, not tracking it here)")
    print("(no separate metric-publishing happens in this script -- the API's "
          "own /metrics is the only signal this produces)\n")

    last_root_cause = 0.0
    while True:
        row = rng.choice(pool)
        send_one(base_url, row)

        if not args.no_root_cause and time.time() - last_root_cause >= ROOT_CAUSE_INTERVAL:
            relay_root_cause(base_url)
            last_root_cause = time.time()

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
