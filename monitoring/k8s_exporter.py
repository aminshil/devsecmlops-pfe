"""
Publishes Kubernetes pod/node status as Prometheus metrics, using kubectl
as the data source rather than scraping the K8s API server directly.
This avoids the networking fragility we hit earlier (Minikube's internal
IP/port can drift after any Docker daemon restart) -- kubectl already
has correct, working access, so we just wrap it.
"""
import json
import subprocess
import time

from prometheus_client import Gauge, start_http_server

TICK_SECONDS = 30
NAMESPACE = "ml-serving"

g_pod_ready = Gauge("k8s_pod_ready", "1 if pod is Ready", ["pod", "namespace"])
g_pod_restarts = Gauge("k8s_pod_restarts", "Container restart count", ["pod", "namespace"])
g_replicas_desired = Gauge("k8s_deployment_replicas_desired", "Desired replica count", ["deployment"])
g_replicas_available = Gauge("k8s_deployment_replicas_available", "Available replica count", ["deployment"])
g_hpa_current = Gauge("k8s_hpa_current_replicas", "Current HPA replica count", ["hpa"])
g_hpa_cpu_target = Gauge("k8s_hpa_cpu_utilization_percent", "Current CPU utilization %", ["hpa"])
g_up = Gauge("k8s_exporter_up", "1 if kubectl commands are succeeding", [])


def kubectl_json(args):
    result = subprocess.run(["kubectl"] + args + ["-o", "json"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def tick():
    try:
        pods = kubectl_json(["get", "pods", "-n", NAMESPACE])
        for item in pods["items"]:
            name = item["metadata"]["name"]
            ready = 0
            for cond in item.get("status", {}).get("conditions", []):
                if cond["type"] == "Ready" and cond["status"] == "True":
                    ready = 1
            restarts = sum(cs.get("restartCount", 0)
                           for cs in item.get("status", {}).get("containerStatuses", []))
            g_pod_ready.labels(pod=name, namespace=NAMESPACE).set(ready)
            g_pod_restarts.labels(pod=name, namespace=NAMESPACE).set(restarts)

        deployments = kubectl_json(["get", "deployment", "-n", NAMESPACE])
        for item in deployments["items"]:
            name = item["metadata"]["name"]
            spec_replicas = item["spec"].get("replicas", 0)
            avail_replicas = item.get("status", {}).get("availableReplicas", 0)
            g_replicas_desired.labels(deployment=name).set(spec_replicas)
            g_replicas_available.labels(deployment=name).set(avail_replicas)

        hpas = kubectl_json(["get", "hpa", "-n", NAMESPACE])
        for item in hpas["items"]:
            name = item["metadata"]["name"]
            current = item.get("status", {}).get("currentReplicas", 0)
            g_hpa_current.labels(hpa=name).set(current)
            for metric in item.get("status", {}).get("currentMetrics", []):
                if metric.get("resource", {}).get("name") == "cpu":
                    util = metric["resource"]["current"].get("averageUtilization", 0)
                    g_hpa_cpu_target.labels(hpa=name).set(util)

        g_up.set(1)
    except Exception as e:
        print(f"[error] {e}")
        g_up.set(0)


if __name__ == "__main__":
    start_http_server(9400)
    print("Kubernetes exporter running: http://localhost:9400/metrics")
    print(f"Checking pods/deployments/HPA every {TICK_SECONDS}s\n")
    while True:
        tick()
        time.sleep(TICK_SECONDS)
