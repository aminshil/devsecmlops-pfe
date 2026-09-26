#!/usr/bin/env python3
"""
production_agent.py -- the ONLY synthetic part of this pipeline is the data
source (real historical readings from the held-out test set, standing in
for a real monitoring agent on a real machine). Everything downstream is
genuine, unmodified production behavior:

    each machine's agent  -->  POST /predict  -->  the API decides + serves
    the result  -->  the API's OWN self-instrumentation (api_predictions_total,
    api_currently_anomalous, api_classifier_p_anomaly, api_input_metric, etc.,
    see api/app.py) reports it  -->  Prometheus scrapes the API directly  -->
    Grafana.

One independent thread PER MACHINE, each on its own ~30s cycle with random
jitter -- not one shared loop picking a random machine every few seconds.
Each agent replays a CONSECUTIVE run of its own machine's real readings, in
time order, keeping the last HISTORY_WINDOW cpu/ram/load_avg values. Once
that window is full, every request carries `history` -- the machine's own
genuine preceding readings -- so the API serves v4 (rolling features), not
just v3. Before the window fills (the first HISTORY_WINDOW-1 ticks after
startup), history is omitted and v3 serves, exactly as a real monitoring
agent would behave on first boot.

Also periodically relays currently-anomalous machines to /root-cause (one
single background thread, not per-machine -- root-cause analysis is
inherently a FLEET-WIDE question). It reads "who is currently anomalous,
per the API's own report" straight from Prometheus, taking the FRESHEST
replica's value per machine: with multiple pods behind the NodePort, each
replica's Gauges only reflect the requests THAT replica served, so a plain
query can return a stale or empty value for a machine currently owned by a
different pod. No scoring or causality logic happens here -- the API
answers both questions entirely on its own; this script only decides WHEN
to ask and reads what the API itself already said.

Deliberately does NOT re-publish results itself (unlike monitoring/
anomaly_bridge.py, a separate, richer demo layer kept as-is, untouched).

Reads data/telecom_fleet_v2_test.csv -- the INDEPENDENT, held-out test
set (never trained on), not the training data monitoring/replay_exporter.py
replays. The more honest "production realism" choice: proves the model
genuinely generalizes to unseen readings.

Sampling seeks to random BYTE OFFSETS (never evenly-spaced -- confirmed
directly that evenly-spaced offsets can alias against periodic structure
in how a file is written) and reads one CONSECUTIVE run per seek, so each
agent's history is the machine's genuine preceding readings, not readings
stitched together from unrelated points in the file.

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
from collections import deque
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "telecom_fleet_v2_test.csv"
FEATURES = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]
HISTORY_KEYS = ("cpu", "ram", "load_avg")   # must match api/app.py HISTORY_KEYS
HISTORY_WINDOW = 10                          # must match api/app.py HISTORY_WINDOW
SEGMENT_ROWS = 500   # consecutive readings sampled per machine (~4h of a 30s-interval demo)
N_SEEKS = 2000        # higher than the number of machines, so seek-sampling covers close to all of them
PROMETHEUS_URL = "http://localhost:9090"
ROOT_CAUSE_INTERVAL = 20  # seconds between /root-cause relays

TARGETS = {
    # Minikube's Docker driver only forwards a small fixed set of ports to
    # localhost -- the NodePort is NOT one of them (confirmed live: the
    # node's own IP is required). 192.168.49.2 is Minikube's own fixed
    # convention, stable across rebuilds -- not a dynamically assigned IP.
    "k8s":  "http://192.168.49.2:30080",
    "host": "http://localhost:8000",
}


def _read_consecutive_run(f, header):
    """From the current file position, read one machine's consecutive rows
    until the machine changes, EOF, or SEGMENT_ROWS is reached."""
    run = []
    for _ in range(SEGMENT_ROWS):
        line = f.readline()
        if not line:
            break
        parts = line.strip().split(",")
        if len(parts) != len(header):
            break
        row = dict(zip(header, parts))
        if run and row.get("machine") != run[0]["machine"]:
            break  # ran into the next machine's rows -- stop here
        run.append(row)
    return run


def build_segments_by_machine(rng, data_path=DATA_PATH):
    """Seek to N_SEEKS random byte offsets; at each, read a CONSECUTIVE run
    of up to SEGMENT_ROWS readings for whichever machine owns that offset
    (the file is written machine-by-machine in time order, so reading
    forward from a random point yields one machine's real, consecutive
    history). Keeps the first full segment found per machine."""
    print(f"Indexing {data_path} (random seeks, consecutive runs, not a full read) ...")
    segments = {}
    size = os.path.getsize(data_path)
    with open(data_path, "r") as f:
        header = f.readline().strip().split(",")
        for _ in range(N_SEEKS):
            f.seek(rng.randint(0, max(size - 1, 0)))
            f.readline()  # discard the partial line we landed inside
            run = _read_consecutive_run(f, header)
            if len(run) == SEGMENT_ROWS and run[0]["machine"] not in segments:
                segments[run[0]["machine"]] = run
    print(f"{len(segments)} machines, {SEGMENT_ROWS} consecutive readings each")
    return segments


def row_to_request(row, history=None):
    # Real hour from the row's own timestamp, never hardcoded -- hardcoding
    # a fixed hour here would score every reading against the wrong
    # per-time-window baseline (the same "3 AM problem" mistake documented
    # elsewhere in this project).
    ts = row.get("timestamp", "")
    try:
        hour = int(ts[11:13])
    except (ValueError, IndexError):
        hour = 14  # last-resort fallback only, never the normal path
    req = {
        "machine": row["machine"],
        "machine_type": row.get("type") or None,
        "hour": hour,
        "metrics": {c: float(row[c]) for c in FEATURES},
    }
    if history is not None:
        req["history"] = history
    return req


def send_one(base_url, row, history=None):
    body = json.dumps(row_to_request(row, history)).encode()
    req = urllib.request.Request(
        base_url + "/predict", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print(f"  {result.get('machine','?'):11} {result.get('model','?'):19} "
                  f"is_anomaly={result.get('is_anomaly')} cause={result.get('likely_cause')}")
    except urllib.error.URLError as e:
        print(f"  {row.get('machine','?'):11} request failed: {e}")


def machine_agent(machine, rows, base_url, interval, stop_event):
    """One independent monitoring agent for one machine: replays its own
    real readings in time order on a jittered cycle, keeping the last
    HISTORY_WINDOW cpu/ram/load_avg values. Once that window is full, every
    request carries the machine's own genuine preceding readings."""
    rng = random.Random(machine)  # deterministic per-machine seed for jitter only
    recent = deque(maxlen=HISTORY_WINDOW)
    stop_event.wait(rng.uniform(0, interval))  # stagger start so agents aren't lockstepped
    i = 0
    while not stop_event.is_set():
        row = rows[i % len(rows)]
        if i and i % len(rows) == 0:
            recent.clear()  # segment wrapped: history restarts, same as a real gap in coverage
        recent.append({c: float(row[c]) for c in HISTORY_KEYS})
        history = ({c: [r[c] for r in recent] for c in HISTORY_KEYS}
                   if len(recent) == HISTORY_WINDOW else None)
        send_one(base_url, row, history)
        i += 1
        stop_event.wait(rng.uniform(interval * 0.7, interval * 1.3))


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


_PODS = 'job="anomaly-api-pods"'
_LAST_SEEN = "api_machine_last_seen_timestamp_seconds{" + _PODS + "}"


def _freshest(metric):
    """Each replica's per-machine Gauges only reflect requests THAT replica
    served, so a plain query can miss or misreport a machine currently
    owned by a different pod. Keep, per machine, the series from the
    replica that saw it most recently -- same rule the Grafana dashboard
    uses (api/app.py's own api_machine_last_seen_timestamp_seconds)."""
    return ("(" + metric + "{" + _PODS + "} and on(pod, machine) (" + _LAST_SEEN +
            " == on(machine) group_left() max by (machine) (" + _LAST_SEEN + ")))")


def relay_root_cause(base_url):
    """Read currently-anomalous machines + their classifier probability
    straight from Prometheus (both self-reported by the API via /predict,
    freshest replica per machine), and ask /root-cause to rank them. All
    ranking logic is server-side; this function only decides WHEN to ask."""
    anomalous = prometheus_query(_freshest("api_currently_anomalous") + " == 1")
    if not anomalous:
        return

    machines = [r["metric"]["machine"] for r in anomalous if "machine" in r["metric"]]
    scores = prometheus_query(_freshest("api_classifier_p_anomaly"))
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
    ap.add_argument("--data", type=Path, default=DATA_PATH,
                     help="dataset to replay (default: the independent test set)")
    args = ap.parse_args()

    base_url = TARGETS[args.target]
    rng = random.SystemRandom()
    by_machine = build_segments_by_machine(rng, args.data)
    if not by_machine:
        print("no rows sampled -- check DATA_PATH / file contents")
        return

    print(f"Starting {len(by_machine)} independent machine agents against {base_url}")
    print(f"Each reports roughly every {args.interval}s (jittered, not synchronized)")
    print(f"History (last {HISTORY_WINDOW} cpu/ram/load_avg readings) sent once each "
          f"agent has that many real readings -- v3 serves until then, v4 after")
    if not args.no_root_cause:
        print(f"Relaying currently-anomalous machines to /root-cause every "
              f"~{ROOT_CAUSE_INTERVAL}s (freshest replica per machine, read FROM Prometheus)")
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
