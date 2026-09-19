"""
Replays the REAL training/evaluation dataset (data/telecom_fleet.csv)
through Prometheus, instead of generating fresh random noise every cycle.

Two modes, selected by the REPLAY_MODE env var:

  realtime  (default) -- sim_replay_hour is always the ACTUAL current
    wall-clock hour (datetime.now().hour). Each tick picks a dataset row
    that was genuinely recorded at that hour, so the values stay realistic
    while the published hour matches real time exactly -- this is how a
    real production agent would behave: one fresh reading every 30s,
    timestamped now, scored against the correct time-of-day baseline.

  fastcycle -- the original behaviour: steps sequentially through the
    whole dataset regardless of wall-clock time, so a full simulated day
    (night -> morning -> afternoon -> evening) plays out in minutes. Use
    this to demo the platform's time-of-day handling (e.g. the "3 AM
    problem") on demand, without waiting for the real clock to get there.

    REPLAY_MODE=fastcycle python3 monitoring/replay_exporter.py

Either way, sim_replay_hour is what the anomaly bridge reads to resolve
the correct baseline window -- see monitoring/anomaly_bridge.py.
"""
import os
import random
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
from prometheus_client import Gauge, start_http_server

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "telecom_fleet.csv"
TICK_SECONDS = 30
REPLAY_MODE = os.environ.get("REPLAY_MODE", "realtime").strip().lower()

g_cpu    = Gauge("sim_cpu_percent",    "Replayed CPU %",         ["machine", "type"])
g_ram    = Gauge("sim_ram_percent",    "Replayed RAM %",         ["machine", "type"])
g_net    = Gauge("sim_network_mbps",   "Replayed network MB/s",  ["machine", "type"])
g_dio    = Gauge("sim_disk_io_percent","Replayed disk IO %",     ["machine", "type"])
g_dusage = Gauge("sim_disk_usage_percent", "Replayed disk usage %", ["machine", "type"])
g_load   = Gauge("sim_load_avg",       "Replayed load average",  ["machine", "type"])
g_truth  = Gauge("sim_ground_truth_anomaly", "REAL label from the dataset: 1=actually anomalous",
                 ["machine", "type"])
g_step   = Gauge("sim_replay_step", "Current position in the replay cycle", [])
g_hour   = Gauge("sim_replay_hour", "Hour of day (0-23) currently being replayed", [])


def load_dataset():
    print(f"Loading {DATA_PATH} ...")
    dtypes = {
        "machine": "category", "type": "category",
        "cpu": "float32", "ram": "float32", "network": "float32",
        "disk_io": "float32", "disk_usage": "float32", "load_avg": "float32",
        "label": "int8",
    }
    df = pd.read_csv(DATA_PATH, usecols=list(dtypes.keys()) + ["timestamp"], dtype=dtypes)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["machine", "timestamp"])

    per_machine = {}
    types = {}
    hours = None
    for machine, grp in df.groupby("machine", observed=True):
        per_machine[machine] = grp[["cpu", "ram", "network", "disk_io",
                                    "disk_usage", "load_avg", "label"]].to_numpy()
        types[machine] = grp["type"].iloc[0]
        if hours is None:
            hours = grp["timestamp"].dt.hour.to_numpy()

    n_steps = min(len(v) for v in per_machine.values())
    print(f"Loaded {len(per_machine)} machines, {n_steps:,} steps each "
         f"({n_steps * TICK_SECONDS / 3600:.1f} hours of real data, replayed continuously)")

    # Row indices grouped by the hour they were actually recorded at, so
    # realtime mode can pick a row that genuinely matches the current hour.
    hour_to_indices = defaultdict(list)
    for i, h in enumerate(hours[:n_steps]):
        hour_to_indices[int(h)].append(i)

    return per_machine, types, n_steps, hours, hour_to_indices


def tick_fastcycle(per_machine, types, step, n_steps, hours):
    """Original behaviour: walk the whole dataset sequentially, so a full
    simulated day plays out in minutes regardless of real time."""
    idx = step % n_steps
    g_step.set(idx)
    g_hour.set(int(hours[idx]))
    _publish_row(per_machine, types, idx)


def tick_realtime(per_machine, types, hour_to_indices):
    """Production-realistic behaviour: sim_replay_hour is the REAL current
    hour. Each tick picks a RANDOM historical occurrence of that hour --
    the dataset spans 30 days, so "hour 17" happened 30 separate times,
    each a contiguous 120-row block (one hour at 30s resolution). Picking
    randomly, rather than walking one block sequentially, means each tick
    is an independent fresh sample of "what does hour 17 typically look
    like" -- not a smooth replay of one specific historical day's episode
    from start to finish. (An earlier version walked sequentially and,
    since 120 rows is exactly one real hour of ticks, ended up replaying
    a single historical day's full multi-router cascade continuously for
    the whole hour -- correct data, but a misleading "always in crisis"
    impression rather than realistic day-to-day variation.)
    Falls back to the nearest hour with data if the exact hour is empty."""
    real_hour = datetime.now().hour
    candidates = hour_to_indices.get(real_hour)
    if not candidates:
        # extremely unlikely (dataset covers all 24h), but fail safe rather
        # than publish nothing: use the nearest hour that DOES have data.
        for delta in range(1, 24):
            for h in (real_hour - delta) % 24, (real_hour + delta) % 24:
                if hour_to_indices.get(h):
                    candidates = hour_to_indices[h]
                    break
            if candidates:
                break

    g_hour.set(real_hour)   # always the true wall-clock hour, even on fallback
    idx = random.choice(candidates)
    g_step.set(idx)
    _publish_row(per_machine, types, idx)


def _publish_row(per_machine, types, idx):
    for machine, arr in per_machine.items():
        mtype = types[machine]
        cpu, ram, net, dio, dusage, load, label = arr[idx]
        g_cpu.labels(machine=machine, type=mtype).set(cpu)
        g_ram.labels(machine=machine, type=mtype).set(ram)
        g_net.labels(machine=machine, type=mtype).set(net)
        g_dio.labels(machine=machine, type=mtype).set(dio)
        g_dusage.labels(machine=machine, type=mtype).set(dusage)
        g_load.labels(machine=machine, type=mtype).set(load)
        g_truth.labels(machine=machine, type=mtype).set(label)


if __name__ == "__main__":
    per_machine, types, n_steps, hours, hour_to_indices = load_dataset()
    start_http_server(9200)
    print(f"Replay exporter running: http://localhost:9200/metrics")
    print(f"Mode: {REPLAY_MODE}")

    if REPLAY_MODE == "fastcycle":
        print(f"Ticking every {TICK_SECONDS}s through real evaluation data "
              f"(simulated clock, independent of wall time)\n")
        step = 0
        while True:
            tick_fastcycle(per_machine, types, step, n_steps, hours)
            step += 1
            time.sleep(TICK_SECONDS)
    else:
        print(f"Ticking every {TICK_SECONDS}s using the REAL current hour "
              f"(sim_replay_hour == datetime.now().hour); each tick is a "
              f"random independent sample of that hour, not a sequential "
              f"replay of one historical day\n")
        while True:
            tick_realtime(per_machine, types, hour_to_indices)
            time.sleep(TICK_SECONDS)
