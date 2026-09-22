#!/usr/bin/env bash
# Full Kubernetes recovery for the DevSecMLOps platform.
# Escalates: gentle start -> if that fails or pods aren't actually ready,
# full delete+recreate+re-apply. Mirrors the manual recovery.
# Logs everything to /tmp/k8s_recover.log.
set -u
MK=/usr/local/bin/minikube
KC=/usr/local/bin/kubectl

# Explicitly set, NOT inherited: this script is invoked from two very
# different contexts -- an interactive login shell (which happens to
# export these already) and the control-panel's systemd service (which
# has no shell profile at all). Without an explicit MINIKUBE_HOME, a
# root-owned invocation silently defaults to /root/.minikube -- a
# different, effectively-empty directory that lacks the pre-populated
# image cache -- causing a full 519 MB re-download even though the real
# cache already has the image. Hardcoding it here means the script
# behaves identically regardless of what triggered it.
export MINIKUBE_HOME=/home/pfe/.minikube
export KUBECONFIG=/home/pfe/.kube/config
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

# The Jenkins container is deliberately attached to the 'minikube' Docker
# network (so its pipeline can reach the cluster's API server directly --
# see the automated-deployment work). Minikube always wants the fixed
# address 192.168.49.2 for itself on that network; if Jenkins is still
# holding it when a fresh cluster container tries to start, creation fails
# with "can't create with that IP, address already in use". Disconnecting
# Jenkins before a (re)start and reconnecting it after is what actually
# fixes this -- confirmed live via `docker network inspect minikube`,
# which showed Jenkins (not a leftover minikube container) holding .2.
disconnect_jenkins() {
  docker network disconnect minikube jenkins >/dev/null 2>&1 || true
}
reconnect_jenkins() {
  docker network connect minikube jenkins >/dev/null 2>&1 || true
}

# Self-heals the base-image cache if something has wiped it (e.g. a stray
# `minikube delete --all --purge`, which clears ~/.minikube entirely,
# cache included). Without this, a fresh cluster creation re-downloads the
# ~519 MB kicbase image from scratch instead of reusing what Docker
# already has pulled. Safe to call unconditionally: it's a no-op if the
# cache file is already there.
BASE_IMG_REF="docker.io/kicbase/stable:v0.0.50@sha256:eb4fec00e8ad70adf8e6436f195cc429825ffb85f95afcdb5d8d9deb576f3e93"
BASE_IMG_CACHE="$MINIKUBE_HOME/cache/images/amd64/kicbase_stable_v0.0.50"
ensure_base_image_cached() {
  if [ -f "$BASE_IMG_CACHE" ]; then
    return 0
  fi
  if docker image inspect "$BASE_IMG_REF" >/dev/null 2>&1; then
    echo "base image cache missing -- repopulating from the local Docker image (no network needed)..."
    mkdir -p "$(dirname "$BASE_IMG_CACHE")"
    docker save "$BASE_IMG_REF" -o "$BASE_IMG_CACHE" 2>&1 | tail -3
  else
    echo "base image cache missing AND not present in Docker -- next start will download it once"
  fi
}
ensure_base_image_cached

# 1) Try a gentle start first (handles a merely-stopped cluster, fast)
echo "[1/5] gentle start attempt..."
timeout 120 "$MK" start --driver=docker --force && {
  if timeout 15 "$KC" get nodes >/dev/null 2>&1; then
    echo "gentle start worked — cluster responding"
    reconnect_jenkins
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
# 2) Disconnect Jenkins (frees 192.168.49.2 -- see disconnect_jenkins above),
# then delete the wedged cluster and force-clear any leftover Docker
# network/container/volume state. Deliberately PLAIN `minikube delete`,
# NOT `--all --purge`: purge wipes the entire ~/.minikube directory,
# including cache/images/ -- exactly what ensure_base_image_cached exists
# to protect. This is also the documented community workaround for the
# long-standing, unresolved upstream "address already in use" bug (see
# kubernetes/minikube#13074, #12894, #13729) -- there is no single flag
# that prevents it; force-removing the container/network/volume before
# the next start is the fix across those threads.
disconnect_jenkins
timeout 120 "$MK" delete 2>&1 | tail -10
docker rm -f minikube >/dev/null 2>&1 || true
docker network rm minikube >/dev/null 2>&1 || true
docker volume rm minikube >/dev/null 2>&1 || true

# 3) Fresh start
echo "[3/5] fresh start..."
timeout 180 "$MK" start --driver=docker --force || { echo "FATAL: fresh start failed"; exit 1; }
reconnect_jenkins

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
