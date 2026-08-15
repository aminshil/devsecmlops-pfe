#!/usr/bin/env bash
# Full Kubernetes recovery for the DevSecMLOps platform.
# Escalates: gentle start -> if that fails, full delete+recreate+re-apply.
# Mirrors exactly the manual recovery. Logs everything to /tmp/k8s_recover.log.
set -u
MK=/usr/local/bin/minikube
KC=/usr/local/bin/kubectl
[ -x "$MK" ] || MK=minikube
[ -x "$KC" ] || KC=kubectl
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=ml-serving
IMG=devsecmlops-api:2.13.0
LOG=/tmp/k8s_recover.log
exec > >(tee -a "$LOG") 2>&1
echo "=== k8s_recover $(date) ==="

# 1) Try a gentle start first (handles a merely-stopped cluster, fast)
echo "[1/5] gentle start attempt..."
timeout 120 "$MK" start --driver=docker --force && {
  # verify the API actually responds
  if timeout 15 "$KC" get nodes >/dev/null 2>&1; then
    echo "gentle start worked — cluster responding"
    # make sure our workloads exist
    if timeout 15 "$KC" get pods -n "$NS" 2>/dev/null | grep -q anomaly-api; then
      echo "workloads present — recovery complete (gentle)"
      exit 0
    fi
    echo "cluster up but workloads missing — applying manifests"
    "$KC" apply -f "$ROOT/kubernetes/namespace.yaml"
    "$KC" apply -f "$ROOT/kubernetes/postgres.yaml"
    "$KC" apply -f "$ROOT/kubernetes/deployment.yaml"
    "$KC" apply -f "$ROOT/kubernetes/service.yaml"
    "$KC" apply -f "$ROOT/kubernetes/hpa.yaml"
    echo "recovery complete (gentle + manifests)"
    exit 0
  fi
}

echo "[2/5] gentle start failed or cluster wedged — full rebuild"
# 2) Delete the wedged cluster
timeout 120 "$MK" delete || { echo "minikube delete failed"; }

# 3) Fresh start
echo "[3/5] fresh start..."
timeout 180 "$MK" start --driver=docker --force || { echo "FATAL: fresh start failed"; exit 1; }

# 4) Load image + metrics-server
echo "[4/5] load image + metrics-server..."
"$MK" image load "$IMG" 2>&1 | tail -2 || echo "image load warning (may already be cached)"
"$MK" addons enable metrics-server 2>&1 | tail -1

# 5) Apply manifests in order
echo "[5/5] apply manifests..."
"$KC" apply -f "$ROOT/kubernetes/namespace.yaml"
"$KC" apply -f "$ROOT/kubernetes/postgres.yaml"
"$KC" apply -f "$ROOT/kubernetes/deployment.yaml"
"$KC" apply -f "$ROOT/kubernetes/service.yaml"
"$KC" apply -f "$ROOT/kubernetes/hpa.yaml"

echo "waiting for pods..."
sleep 25
"$KC" get pods -n "$NS"
echo "=== recovery complete (full rebuild) $(date) ==="
exit 0
