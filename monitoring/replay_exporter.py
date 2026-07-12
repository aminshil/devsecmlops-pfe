"""
Replays the REAL training/evaluation dataset (data/telecom_fleet.csv)
through Prometheus, instead of generating fresh random noise every cycle.

Publishes sim_replay_hour so the anomaly bridge can use the CORRECT
simulated hour (matching the replayed data) instead of real wall-clock
time -- the two are completely independent timelines.
"""
import time
from pathlib import Path

import pandas as pd
from prometheus_client import Gauge, start_http_server

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "telecom_fleet.csv"
TICK_SECONDS = 30

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
    return per_machine, types, n_steps, hours


def tick(per_machine, types, step, n_steps, hours):
    idx = step % n_steps
    g_step.set(idx)
    g_hour.set(int(hours[idx]))
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
    per_machine, types, n_steps, hours = load_dataset()
    start_http_server(9200)
    print(f"Replay exporter running: http://localhost:9200/metrics")
    print(f"Ticking every {TICK_SECONDS}s through real evaluation data\n")

    step = 0
    while True:
        tick(per_machine, types, step, n_steps, hours)
        step += 1
        time.sleep(TICK_SECONDS)
