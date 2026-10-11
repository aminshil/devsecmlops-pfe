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
  KC="$KC" NS="$NS" "$ROOT/scripts/ensure_db_secret.sh"
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
disconnect_ip_holder() {
  # Generalized fix: any container that has ever been connected to the
  # 'minikube' network could be squatting on the fixed IP the cluster
  # container always wants for itself. Named-container disconnects
  # (e.g. "always disconnect jenkins") are fragile -- tonight alone,
  # jenkins, its renamed jenkins-old rollback copy, AND registry each
  # independently ended up holding it at different points, and none of
  # them share a name. Find whoever CURRENTLY holds .2 and evict them,
  # regardless of what they're called -- this is the actual invariant
  # that matters, not any one container's identity.
  local holder
  holder="$(docker network inspect minikube \
    --format '{{range $k,$v := .Containers}}{{if eq $v.IPv4Address "192.168.49.2/24"}}{{$v.Name}}{{end}}{{end}}' \
    2>/dev/null)"
  if [ "$holder" = "minikube" ]; then
    return 0   # the cluster container legitimately owns .2 -- never evict it
  fi
  if [ -n "$holder" ]; then
    echo "disconnecting '$holder' from the minikube network (holding 192.168.49.2, which the cluster container needs for itself)"
    docker network disconnect minikube "$holder" >/dev/null 2>&1 || true
  fi
}
PIN_JENKINS_IP="${PIN_JENKINS_IP:-192.168.49.10}"
reconnect_jenkins() {
  # Pinned so Jenkins can never take 192.168.49.2 from the cluster after a reboot.
  local cur
  cur="$(docker inspect jenkins --format '{{with index .NetworkSettings.Networks "minikube"}}{{.IPAddress}}{{end}}' 2>/dev/null)"
  if [ "$cur" = "$PIN_JENKINS_IP" ]; then return 0; fi
  [ -n "$cur" ] && docker network disconnect minikube jenkins >/dev/null 2>&1 || true
  docker network connect --ip "$PIN_JENKINS_IP" minikube jenkins >/dev/null 2>&1 || true
}

# ---- operator feedback survives a cluster rebuild ---------------------------
# A rebuild deletes the Docker volume that holds PostgreSQL. Judged rows are
# dumped first and put back afterwards. The dump only replaces the saved file
# when it is non-empty, so a wedged cluster can never overwrite a good backup.
FEEDBACK_BACKUP="${FEEDBACK_BACKUP:-$(dirname "$MINIKUBE_HOME")/feedback_backup.tsv}"
backup_feedback() {
  local tmp="${FEEDBACK_BACKUP}.new"
  if timeout 40 "$KC" -n "$NS" exec -i postgres-0 -- sh -c \
      'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -qAt -c "COPY (SELECT * FROM predictions WHERE operator_verdict IS NOT NULL) TO STDOUT"' \
      > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
    mv "$tmp" "$FEEDBACK_BACKUP"
    echo "feedback backed up: $(wc -l < "$FEEDBACK_BACKUP") judged rows -> $FEEDBACK_BACKUP"
  else
    rm -f "$tmp"
    if [ -s "$FEEDBACK_BACKUP" ]; then
      echo "cluster DB not readable -- keeping the previous backup ($(wc -l < "$FEEDBACK_BACKUP") rows)"
    else
      echo "no feedback backup available (nothing judged yet, or DB unreadable)"
    fi
  fi
}
restore_feedback() {
  [ -s "$FEEDBACK_BACKUP" ] || { echo "no feedback backup to restore"; return 0; }
  local i n
  echo "waiting for postgres + the predictions table before restoring feedback..."
  for i in $(seq 1 60); do
    if "$KC" -n "$NS" exec postgres-0 -- sh -c \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qAt -c "SELECT 1 FROM predictions LIMIT 1"' >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done
  n="$("$KC" -n "$NS" exec postgres-0 -- sh -c \
      'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qAt -c "SELECT count(*) FROM predictions"' 2>/dev/null || echo x)"
  if [ "$n" != "0" ]; then
    echo "predictions table not empty or unreadable (count=$n) -- not restoring"
    return 0
  fi
  if "$KC" -n "$NS" exec -i postgres-0 -- sh -c \
      'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q -c "COPY predictions FROM STDIN"' \
      < "$FEEDBACK_BACKUP"; then
    echo "feedback restored: $(wc -l < "$FEEDBACK_BACKUP") judged rows"
    # if the id column is a serial, move its sequence past the restored ids
    printf '%s\n' "SELECT setval(pg_get_serial_sequence('predictions','id'), (SELECT max(id) FROM predictions)) WHERE pg_get_serial_sequence('predictions','id') IS NOT NULL" |
      "$KC" -n "$NS" exec -i postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qAt' >/dev/null 2>&1 || true
  else
    echo "WARNING: feedback restore failed -- backup kept at $FEEDBACK_BACKUP"
  fi
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

# Jenkins bind-mounts $MINIKUBE_HOME and $KUBECONFIG's directory so its
# deploy stage can reach the cluster. Minikube runs as root, so every fresh
# cluster recreates its profile files owned by root only, which removes
# Jenkins' access again. This re-grants access after every successful start.
#
# Access is granted with a POSIX ACL to ONE uid only -- the jenkins user
# inside the Jenkins container (uid 1000, which is also the VM's login
# user) -- never world-readable/writable: this directory holds the cluster
# CA private key and admin client credentials. The default ACL (-d) makes
# files that Minikube creates later inherit the same grant. "Other" users
# get nothing.
JENKINS_UID="${JENKINS_UID:-1000}"
fix_minikube_home_perms() {
  if ! command -v setfacl >/dev/null 2>&1; then
    echo "WARNING: setfacl missing (apt install acl) -- Jenkins deploy access not granted"
    return 0
  fi
  chmod -R o-rwx "$MINIKUBE_HOME" "$(dirname "$KUBECONFIG")" 2>/dev/null || true
  setfacl -R -m "u:${JENKINS_UID}:rwX" -m "d:u:${JENKINS_UID}:rwX" \
    "$MINIKUBE_HOME" "$(dirname "$KUBECONFIG")" 2>/dev/null || true
}

# 1) Try a gentle start first (handles a merely-stopped cluster, fast)
disconnect_ip_holder
echo "[1/5] gentle start attempt..."
timeout 240 "$MK" start --driver=docker --force && {
  if timeout 15 "$KC" get nodes >/dev/null 2>&1; then
    echo "gentle start worked — cluster responding"
    reconnect_jenkins
    fix_minikube_home_perms
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
# 2) Free 192.168.49.2 (see disconnect_ip_holder above -- evicts whoever
# then delete the wedged cluster and force-clear any leftover Docker
# network/container/volume state. Deliberately PLAIN `minikube delete`,
# NOT `--all --purge`: purge wipes the entire ~/.minikube directory,
# including cache/images/ -- exactly what ensure_base_image_cached exists
# to protect. This is also the documented community workaround for the
# long-standing, unresolved upstream "address already in use" bug (see
# kubernetes/minikube#13074, #12894, #13729) -- there is no single flag
# that prevents it; force-removing the container/network/volume before
# the next start is the fix across those threads.
backup_feedback
disconnect_ip_holder
timeout 120 "$MK" delete 2>&1 | tail -10
docker rm -f minikube >/dev/null 2>&1 || true
docker network rm minikube >/dev/null 2>&1 || true
docker volume rm minikube >/dev/null 2>&1 || true

# 3) Fresh start
echo "[3/5] fresh start..."
timeout 180 "$MK" start --driver=docker --force || { echo "FATAL: fresh start failed"; exit 1; }
reconnect_jenkins
fix_minikube_home_perms

# 4) Load image + metrics-server
echo "[4/5] load image + metrics-server..."
"$MK" image load "$IMG" --overwrite 2>&1 | tail -3
"$MK" addons enable metrics-server 2>&1 | tail -1

# 5) Apply manifests and confirm real readiness
echo "[5/5] apply manifests..."
apply_manifests
restore_feedback

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
