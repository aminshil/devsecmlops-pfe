"""
Simulates 200 machines' worth of metrics and exposes them all as ONE
Prometheus-format /metrics endpoint. Prometheus scrapes this exactly
like a real Node Exporter -- it has no way to tell the difference.

Reuses the same per-type profiles as generate_telecom_fleet.py so the
simulated fleet behaves consistently with what the model was trained on.
Occasionally injects a real anomaly on a random machine so the demo has
something to catch.
"""
import random
import sys
import time
from pathlib import Path
from datetime import datetime

from prometheus_client import Gauge, start_http_server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ml-model"))
from generate_telecom_fleet import PROFILES, NETWORK_GEAR, build_fleet
import json

FLEET = build_fleet(200)

GRAPH_PATH = Path(__file__).resolve().parent.parent / "models" / "dependency_graph.json"
with open(GRAPH_PATH) as f:
    DEP_GRAPH = json.load(f)
DOWNSTREAM_OF = DEP_GRAPH["downstream_of"]  # router -> [machines]

# One Gauge per feature, labeled by machine + type (Prometheus multi-series pattern)
g_cpu    = Gauge("sim_cpu_percent",    "Simulated CPU %",         ["machine", "type"])
g_ram    = Gauge("sim_ram_percent",    "Simulated RAM %",         ["machine", "type"])
g_net    = Gauge("sim_network_mbps",   "Simulated network MB/s",  ["machine", "type"])
g_dio    = Gauge("sim_disk_io_percent","Simulated disk IO %",     ["machine", "type"])
g_dusage = Gauge("sim_disk_usage_percent", "Simulated disk usage %", ["machine", "type"])
g_load   = Gauge("sim_load_avg",       "Simulated load average",  ["machine", "type"])

# state: currently injected anomaly (machine_name, remaining_ticks) or None
anomaly_state = {"machine": None, "ticks_left": 0, "affected": []}


def time_factor(hour):
    if 8 <= hour < 20:
        return 1.0
    return 0.4


def tick():
    hour = datetime.now().hour
    tf = time_factor(hour)

    # Occasionally start a new anomaly (5% chance per tick, if none active)
    if anomaly_state["ticks_left"] <= 0 and random.random() < 0.08:
        routers = [n for n, p, _ in FLEET if p == "router"]
        name = random.choice(routers)
        anomaly_state["machine"] = name
        anomaly_state["ticks_left"] = random.randint(5, 15)
        deps = DOWNSTREAM_OF.get(name, [])
        anomaly_state["affected"] = random.sample(deps, min(4, len(deps)))
        print(f"[anomaly injected] {name} (router) -> stressing {anomaly_state['affected']} "
             f"for {anomaly_state['ticks_left']} ticks")

    for name, pname, profile in FLEET:
        ((cpu_mu, cpu_sig), (ram_mu, ram_sig), (net_mu, net_sig),
         (dio_mu, dio_sig), (dusage_base, dusage_sig), (load_mu, load_sig),
         night_factor, _) = profile
        is_gear = pname in NETWORK_GEAR

        cpu = max(0, min(100, random.gauss(cpu_mu * tf, cpu_sig)))
        ram = max(0, min(100, random.gauss(ram_mu * tf, ram_sig)))
        net = max(0, random.gauss(net_mu * tf, net_sig))
        dio = 0 if is_gear else max(0, min(100, random.gauss(dio_mu * tf, dio_sig)))
        dusage = 0 if is_gear else max(0, min(100, random.gauss(dusage_base, dusage_sig)))
        load = max(0, random.gauss(load_mu * tf, load_sig))

        if anomaly_state["ticks_left"] > 0:
            if name == anomaly_state["machine"]:
                cpu = min(100, cpu * 2.5)
                ram = min(100, ram * 1.5)
                load = load * 3
            elif name in anomaly_state["affected"]:
                net = net * 3.5
                load = load * 3.0
                cpu = min(100, cpu * 2.2)
                ram = min(100, ram * 1.6)

        g_cpu.labels(machine=name, type=pname).set(round(cpu, 2))
        g_ram.labels(machine=name, type=pname).set(round(ram, 2))
        g_net.labels(machine=name, type=pname).set(round(net, 2))
        g_dio.labels(machine=name, type=pname).set(round(dio, 2))
        g_dusage.labels(machine=name, type=pname).set(round(dusage, 2))
        g_load.labels(machine=name, type=pname).set(round(load, 2))

    if anomaly_state["ticks_left"] > 0:
        anomaly_state["ticks_left"] -= 1
        if anomaly_state["ticks_left"] == 0:
            print(f"[anomaly cleared] {anomaly_state['machine']}")
            anomaly_state["machine"] = None
            anomaly_state["affected"] = []


if __name__ == "__main__":
    start_http_server(9200)
    print(f"Fleet simulator running: http://localhost:9200/metrics")
    print(f"Simulating {len(FLEET)} machines, updating every 30s\n")
    while True:
        tick()
        time.sleep(30)
