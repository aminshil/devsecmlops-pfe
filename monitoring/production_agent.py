#!/usr/bin/env python3
"""
production_agent.py -- the ONLY synthetic part of this pipeline is the data
source (real historical readings from the held-out test set, standing in
for a real monitoring agent on a real machine). Everything downstream is
genuine, unmodified production behavior:

    each machine's agent  -->  POST /predict  -->  the API decides + serves
    the result  -->  the API's OWN self-instrumentation (api_predictions_total,
    api_currently_anomalous, api_anomaly_score, api_input_metric, etc., see
    api/app.py) reports it  -->  Prometheus scrapes the API directly  -->
    Grafana.

One independent thread PER MACHINE, each on its own ~30s cycle with random
jitter -- not one shared loop picking a random machine every few seconds.
In a real fleet, every machine runs its own monitoring agent on its own
schedule; they are not perfectly synchronized with each other. Threads are
cheap here because each one spends almost all its time asleep or waiting
on network I/O, not doing real work -- 200 of them costs essentially
nothing.

Also periodically relays currently-anomalous machines to /root-cause (one
single background thread, not per-machine -- root-cause analysis is
inherently a FLEET-WIDE question, not something one machine's agent would
ask on its own). It reads "who is currently anomalous, per the API's own
report" straight from Prometheus (api_currently_anomalous{...}==1), and
asks /root-cause to rank them. No scoring, no causality logic, no
computation happens here -- the API answers both questions (is this
anomalous? / who's the real cause?) entirely on its own; this script only
decides WHEN to ask.

Deliberately does NOT re-publish results itself (unlike monitoring/
anomaly_bridge.py, a separate, richer demo layer kept as-is, untouched).
This script's only job is to look, from the API's point of view, exactly
like real callers.

Reads data/telecom_fleet_v2_test.csv -- the INDEPENDENT, held-out test
set (never trained on), not the training data monitoring/replay_exporter.py
replays. This is the more honest "production realism" choice: it proves
the model genuinely generalizes to unseen readings, not that it recognizes
memorized training rows.

Sampling deliberately mirrors api/app.py's own _load_sample_pool /
_collect_samples: this file can be very large (a multi-day, 30s-resolution,
200-machine simulation), so this seeks to random BYTE OFFSETS across the
file rather than reading it in full. Rows are then grouped by machine, so
each machine's agent thread cycles only through THAT machine's own real
historical readings, never another machine's.

Usage:
    python3 monitoring/production_agent.py                    # -> K8s NodePort
    python3 monitoring/production_agent.py --target host       # -> host API :8000
    python3 monitoring/production_agent.py --interval 30       # avg seconds between EACH machine's own reports
    python3 monitoring/production_agent.py --no-root-cause     # skip the /root-cause relay
"""
import argparse
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "telecom_fleet_v2_test.csv"
FEATURES = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]
N_SEEKS = 2000  # higher than a single-agent design needs, so seek-sampling
                # covers close to the full 200-machine fleet, not just a subset
PROMETHEUS_URL = "http://localhost:9090"
ROOT_CAUSE_INTERVAL = 20  # seconds between /root-cause relays

TARGETS = {
    # Minikube's Docker driver only forwards a small fixed set of ports to
    # localhost -- the NodePort is NOT one of them (confirmed live: the
    # node's own IP is required, see the README "operational incident"
    # entry this discovery led to). 192.168.49.2 is Minikube's own fixed
    # convention, stable across rebuilds -- not a dynamically assigned IP.
    "k8s":  "http://192.168.49.2:30080",
    "host": "http://localhost:8000",
}


def build_pool_by_machine(rng):
    """Seek to N_SEEKS positions across the file; at each, read one row.
    Same technique as api/app.py's _collect_samples -- never reads the
    file in full. Grouped by machine so each machine's agent thread only
    ever sends readings that were genuinely recorded for that machine."""
    print(f"Indexing {DATA_PATH} (seek-sampling, not a full read) ...")
    by_machine = defaultdict(list)
    with open(DATA_PATH, "r") as f:
        header = f.readline().strip().split(",")
        size = os.path.getsize(DATA_PATH)
        # Genuinely RANDOM seek positions, not evenly-spaced (size * k / N_SEEKS)
        # -- evenly-spaced offsets can silently ALIAS against any periodic
        # structure in how the file was written (confirmed directly: a
        # regularly-cycling test file caused evenly-spaced seeks to land on
        # the same 2 of 20 machines repeatedly, since the seek stride shared
        # a common factor with the machine-assignment cycle -- the same
        # aliasing effect as a strobe light synced to a rotating wheel).
        # Real data may not be this perfectly periodic, but random offsets
        # are immune to the failure mode regardless, at no extra cost.
        for _ in range(N_SEEKS):
            f.seek(rng.randint(0, max(size - 1, 0)))
            f.readline()  # discard the partial line we landed inside
            line = f.readline().strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != len(header):
                continue
            row = dict(zip(header, parts))
            m = row.get("machine")
            if m:
                by_machine[m].append(row)
    for rows in by_machine.values():
        rng.shuffle(rows)
    print(f"Sampled {sum(len(v) for v in by_machine.values())} rows across "
          f"{len(by_machine)} distinct machines (from {N_SEEKS} seek points)")
    return dict(by_machine)


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
        print(f"  {row.get('machine','?'):10} request failed: {e}")


def machine_agent(machine, rows, base_url, interval, stop_event):
    """One independent 'monitoring agent' for a single machine: wakes up
    on its own roughly-`interval`-second cycle (with jitter, so it is not
    lockstepped with every other machine's agent) and reports one of its
    own real historical readings."""
    rng = random.Random(f"{machine}-{id(rows)}")  # deterministic per-machine seed for jitter only
    # Stagger initial start so 200 threads don't all fire in the same instant
    stop_event.wait(rng.uniform(0, interval))
    while not stop_event.is_set():
        row = rng.choice(rows)
        send_one(base_url, row)
        jitter = rng.uniform(interval * 0.7, interval * 1.3)
        stop_event.wait(jitter)


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


def root_cause_loop(base_url, stop_event):
    while not stop_event.is_set():
        stop_event.wait(ROOT_CAUSE_INTERVAL)
        if not stop_event.is_set():
            relay_root_cause(base_url)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", choices=list(TARGETS), default="k8s",
                     help="which serving path to hit (default: k8s, the NodePort)")
    ap.add_argument("--interval", type=float, default=30.0,
                     help="average seconds between EACH machine's own reports (default: 30)")
    ap.add_argument("--no-root-cause", action="store_true",
                     help="skip the periodic /root-cause relay")
    args = ap.parse_args()

    base_url = TARGETS[args.target]
    rng = random.SystemRandom()
    by_machine = build_pool_by_machine(rng)
    if not by_machine:
        print("no rows sampled -- check DATA_PATH / file contents")
        return

    print(f"Starting {len(by_machine)} independent machine agents against {base_url}")
    print(f"Each reports roughly every {args.interval}s (jittered, not synchronized)")
    if not args.no_root_cause:
        print(f"Relaying currently-anomalous machines to /root-cause every "
              f"~{ROOT_CAUSE_INTERVAL}s (reading state FROM Prometheus, not tracking it here)")
    print("(no separate metric-publishing happens in this script -- the API's "
          "own /metrics is the only signal this produces)\n")

    stop_event = threading.Event()
    threads = []
    for machine, rows in by_machine.items():
        t = threading.Thread(
            target=machine_agent, args=(machine, rows, base_url, args.interval, stop_event),
            daemon=True, name=f"agent-{machine}"
        )
        t.start()
        threads.append(t)

    if not args.no_root_cause:
        rc_thread = threading.Thread(
            target=root_cause_loop, args=(base_url, stop_event), daemon=True, name="root-cause-relay"
        )
        rc_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping all agents...")
        stop_event.set()
        for t in threads:
            t.join(timeout=2)


if __name__ == "__main__":
    main()
