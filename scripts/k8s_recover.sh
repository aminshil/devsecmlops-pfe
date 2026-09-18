#!/usr/bin/env bash
# Full Kubernetes recovery for the DevSecMLOps platform.
# Escalates: gentle start -> if that fails or pods aren't actually ready,
# full delete+recreate+re-apply. Mirrors the manual recovery.
# Logs everything to /tmp/k8s_recover.log.
set -u
MK=/usr/local/bin/minikube
KC=/usr/local/bin/kubectl
[ -x "$MK" ] || MK=minikube
[ -x "$KC" ] || KC=kubectl
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=ml-serving
LOG=/tmp/k8s_recover.log
exec > >(tee -a "$LOG") 2>&1
echo "=== k8s_recover $(date) ==="

# Image tag is read from the manifest itself -- NEVER hardcoded here.
# A hardcoded tag silently goes stale every time the app is re-versioned
# (this is exactly what broke recovery repeatedly before this fix: the
# script kept loading an old tag while the manifest asked for a newer one).
IMG="$(grep -oP '(?<=image: )devsecmlops-api:\S+' "$ROOT/kubernetes/deployment.yaml" | head -1)"
if [ -z "$IMG" ]; then
  echo "FATAL: could not read the image tag from kubernetes/deployment.yaml"
  exit 1
fi
echo "target image: $IMG"

apply_manifests() {
  "$KC" apply -f "$ROOT/kubernetes/namespace.yaml"
  "$KC" apply -f "$ROOT/kubernetes/postgres-secret.yaml"
  "$KC" apply -f "$ROOT/kubernetes/postgres.yaml"
  "$KC" apply -f "$ROOT/kubernetes/deployment.yaml"
  "$KC" apply -f "$ROOT/kubernetes/service.yaml"
  "$KC" apply -f "$ROOT/kubernetes/hpa.yaml"
}

# Genuine readiness check -- NOT just "a pod with this name exists".
# A Pending/ErrImageNeverPull pod matches a name grep too; only a real
# rollout-status wait proves the deployment is actually serving.
pods_actually_ready() {
  timeout 70 "$KC" rollout status deployment/anomaly-api -n "$NS" --timeout=60s >/dev/null 2>&1
}

ensure_image_loaded() {
  if ! "$MK" image ls 2>/dev/null | grep -q "$IMG"; then
    echo "image $IMG not in the cluster's store -- loading..."
    "$MK" image load "$IMG" --overwrite 2>&1 | tail -3
  else
    echo "image $IMG already present in the cluster's store"
  fi
}

# 1) Try a gentle start first (handles a merely-stopped cluster, fast)
echo "[1/5] gentle start attempt..."
timeout 120 "$MK" start --driver=docker --force && {
  if timeout 15 "$KC" get nodes >/dev/null 2>&1; then
    echo "gentle start worked — cluster responding"
    ensure_image_loaded
    if ! "$KC" get pods -n "$NS" 2>/dev/null | grep -q anomaly-api; then
      echo "workloads missing — applying manifests"
      apply_manifests
    fi
    echo "waiting for the deployment to actually become ready..."
    if pods_actually_ready; then
      echo "workloads confirmed ready — recovery complete (gentle)"
      "$KC" get pods -n "$NS"
      exit 0
    fi
    echo "pods still not ready after gentle path — re-applying and re-checking"
    apply_manifests
    if pods_actually_ready; then
      echo "workloads confirmed ready — recovery complete (gentle + reapply)"
      "$KC" get pods -n "$NS"
      exit 0
    fi
    echo "still not ready — escalating to full rebuild"
  fi
}

echo "[2/5] gentle start failed or cluster still not serving — full rebuild"
# 2) Delete the wedged cluster
timeout 120 "$MK" delete || { echo "minikube delete failed"; }

# 3) Fresh start
echo "[3/5] fresh start..."
timeout 180 "$MK" start --driver=docker --force || { echo "FATAL: fresh start failed"; exit 1; }

# 4) Load image + metrics-server
echo "[4/5] load image + metrics-server..."
"$MK" image load "$IMG" --overwrite 2>&1 | tail -3
"$MK" addons enable metrics-server 2>&1 | tail -1

# 5) Apply manifests and confirm real readiness
echo "[5/5] apply manifests..."
apply_manifests

echo "waiting for pods to become ready..."
if pods_actually_ready; then
  echo "recovery complete (full rebuild) — workloads confirmed ready"
else
  echo "WARNING: pods still not ready after full rebuild — check manually:"
  echo "  kubectl describe pod -n $NS -l app=anomaly-api"
fi
"$KC" get pods -n "$NS"
echo "=== recovery finished $(date) ==="
exit 0
