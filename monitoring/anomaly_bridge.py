"""
Anomaly bridge: polls Prometheus for all 200 simulated machines' metrics,
calls /predict for each, and if multiple machines are anomalous at once,
batches them into /root-cause.

Publishes its own results back to Prometheus (port 9300) so Grafana can
visualize live detection state, not just raw fleet metrics.
"""
import time
from datetime import datetime

import requests
from prometheus_client import Gauge, start_http_server

g_is_anomaly = Gauge("bridge_is_anomaly", "1 if machine is currently anomalous",
                     ["machine", "role", "type"])
g_anomaly_score = Gauge("bridge_anomaly_score", "Current anomaly score",
                        ["machine", "type"])
g_anomaly_count = Gauge("bridge_anomaly_count", "Total anomalous machines this cycle", [])

PROM_URL = "http://localhost:9090"
API_URL  = "http://localhost:8000"
INTERVAL = 30

FEATURE_METRICS = {
    "cpu":         "sim_cpu_percent",
    "ram":         "sim_ram_percent",
    "network":     "sim_network_mbps",
    "disk_io":     "sim_disk_io_percent",
    "disk_usage":  "sim_disk_usage_percent",
    "load_avg":    "sim_load_avg",
}


def prom_query(promql: str):
    r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": promql}, timeout=10)
    r.raise_for_status()
    return r.json()["data"]["result"]


def get_replay_hour():
    """Read the CURRENT simulated hour from the replay exporter, not real
    wall-clock time -- the replay timeline is independent of real time."""
    result = prom_query("sim_replay_hour")
    if result:
        return int(float(result[0]["value"][1]))
    return datetime.now().hour  # fallback if replay exporter isn't running


def collect_all_machines():
    """Returns {machine_name: {"metrics": {...}, "type": "web"}, ...} for all 200 machines."""
    machines = {}
    for feature, metric_name in FEATURE_METRICS.items():
        for series in prom_query(metric_name):
            name = series["metric"]["machine"]
            mtype = series["metric"].get("type", "unknown")
            value = float(series["value"][1])
            entry = machines.setdefault(name, {"metrics": {}, "type": mtype})
            entry["metrics"][feature] = round(value, 2)
    # only keep machines with all 6 features present
    return {m: v for m, v in machines.items() if len(v["metrics"]) == 6}


def predict(machine, metrics, hour):
    resp = requests.post(f"{API_URL}/predict",
                         json={"machine": machine, "metrics": metrics, "hour": hour},
                         timeout=10)
    resp.raise_for_status()
    return resp.json()


def root_cause(anomalies: dict):
    resp = requests.post(f"{API_URL}/root-cause",
                         json={"anomalies": anomalies},
                         timeout=10)
    resp.raise_for_status()
    return resp.json()


def run_cycle():
    hour = get_replay_hour()
    machines = collect_all_machines()
    g_is_anomaly.clear()
    g_anomaly_score.clear()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Evaluating {len(machines)} machines...")

    anomalies = {}       # machine -> score
    machine_types = {}   # machine -> type
    for name, entry in machines.items():
        machine_types[name] = entry["type"]
        try:
            result = predict(name, entry["metrics"], hour)
            if result["is_anomaly"]:
                anomalies[name] = result["anomaly_score"]
        except Exception as e:
            print(f"  [error predicting {name}] {e}")

    g_anomaly_count.set(len(anomalies))
    for m, score in anomalies.items():
        g_anomaly_score.labels(machine=m, type=machine_types[m]).set(score)

    if not anomalies:
        print("  No anomalies detected.\n")
        return

    print(f"  {len(anomalies)} anomalous machine(s): {list(anomalies.keys())}")

    if len(anomalies) > 1:
        rc = root_cause(anomalies)
        print(f"  Likely root cause(s): {rc['likely_root_causes']}")
        for r in rc["ranked"]:
            print(f"    {r['machine']:<12} role={r['role']:<18} score={r['own_score']}")
            g_is_anomaly.labels(machine=r["machine"], role=r["role"],
                                type=machine_types[r["machine"]]).set(1)
    else:
        (name, score), = anomalies.items()
        print(f"  Single anomaly: {name} (score={score})")
        g_is_anomaly.labels(machine=name, role="isolated", type=machine_types[name]).set(1)
    print()


def main_loop():
    start_http_server(9300)
    print(f"Anomaly bridge started (metrics on :9300). Evaluating all machines every {INTERVAL}s.\n")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print(f"[cycle error] {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main_loop()
