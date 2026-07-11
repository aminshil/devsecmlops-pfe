"""
Anomaly bridge: polls Prometheus for all 200 simulated machines' metrics,
calls /predict for each, and if multiple machines are anomalous at once,
batches them into /root-cause.

This is the piece that closes the loop on "detect problems before
downtime": Prometheus collects metrics continuously, this bridge
evaluates all 200 machines against the trained model every 30 seconds,
and surfaces both individual anomalies and their likely root cause.
"""
import time
from datetime import datetime

import requests

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


def collect_all_machines():
    """Returns {machine_name: {feature: value, ...}, ...} for all 200 machines."""
    machines = {}
    for feature, metric_name in FEATURE_METRICS.items():
        for series in prom_query(metric_name):
            name = series["metric"]["machine"]
            value = float(series["value"][1])
            machines.setdefault(name, {})[feature] = round(value, 2)
    # only keep machines with all 6 features present
    return {m: v for m, v in machines.items() if len(v) == 6}


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
    hour = datetime.now().hour
    machines = collect_all_machines()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Evaluating {len(machines)} machines...")

    anomalies = {}
    for name, metrics in machines.items():
        try:
            result = predict(name, metrics, hour)
            if result["is_anomaly"]:
                anomalies[name] = result["anomaly_score"]
        except Exception as e:
            print(f"  [error predicting {name}] {e}")

    if not anomalies:
        print("  No anomalies detected.\n")
        return

    print(f"  {len(anomalies)} anomalous machine(s): {list(anomalies.keys())}")

    if len(anomalies) > 1:
        rc = root_cause(anomalies)
        print(f"  Likely root cause(s): {rc['likely_root_causes']}")
        for r in rc["ranked"][:5]:
            print(f"    {r['machine']:<12} role={r['role']:<18} score={r['own_score']}")
    else:
        (name, score), = anomalies.items()
        print(f"  Single anomaly: {name} (score={score})")
    print()


def main_loop():
    print(f"Anomaly bridge started. Evaluating all machines every {INTERVAL}s.\n")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print(f"[cycle error] {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main_loop()
