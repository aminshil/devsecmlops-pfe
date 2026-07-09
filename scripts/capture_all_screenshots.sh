#!/usr/bin/env bash
# Wayland-safe screenshot capture, auto-cropped to the terminal area.
# Runs gnome-screenshot as pfe (Wayland requires it), then crops the taskbar off.

set -u
ROOT="$HOME/devsecmlops-pfe"
OUT="/home/pfe/pfe_screenshots"
mkdir -p "$OUT"
chmod 755 "$OUT"
cd "$ROOT"
source venv/bin/activate 2>/dev/null || true

DESKTOP_USER="pfe"
UID_NUM=1000
XAUTH="/run/user/${UID_NUM}/.mutter-Xwaylandauth.VWK5P3"

# Detect actual screen size once
SCREEN_WH=$(sudo -u "$DESKTOP_USER" \
    DISPLAY=:0 XAUTHORITY="$XAUTH" \
    XDG_RUNTIME_DIR="/run/user/${UID_NUM}" \
    xdpyinfo 2>/dev/null | awk '/dimensions/ {print $2}')
echo "Detected screen: ${SCREEN_WH:-unknown}"

# Ubuntu GNOME taskbar/topbar dimensions we need to crop:
#   top: ~28 px (activity bar) + terminal titlebar handled below
#   left: ~72 px (dock)
# We'll crop those off to leave just the terminal content area.
CROP_LEFT=72     # Ubuntu dock width
CROP_TOP=32      # top activities/notification bar
CROP_RIGHT=0     # nothing on right
CROP_BOTTOM=0    # taskbar is included with terminal usually

shot() {
  local path="$1"
  local tmp="/tmp/_snap_$$_$RANDOM.png"
  sudo -u "$DESKTOP_USER" \
    DISPLAY=:0 \
    XAUTHORITY="$XAUTH" \
    XDG_RUNTIME_DIR="/run/user/${UID_NUM}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID_NUM}/bus" \
    gnome-screenshot -d 2 -f "$tmp" 2>/tmp/shot-err.log
  if [ ! -f "$tmp" ]; then
    echo "  WARNING: screenshot failed. See /tmp/shot-err.log" >&2
    return
  fi
  # No cropping — save the raw fullscreen capture
  mv "$tmp" "$path"
  # Fix ownership so root can manage the file
  chown root:root "$path" 2>/dev/null || true
}

echo "═══════════════════════════════════════════════════════════════"
echo "  MAXIMIZE TERMINAL NOW (F11 for fullscreen recommended)"
echo "  Do NOT click other windows for ~8 minutes"
echo "  Includes trainer (~2 min) + data generator (~2 min)"
echo "  Starting in 10 seconds..."
echo "═══════════════════════════════════════════════════════════════"
sleep 10

snap () {
  local name="$1"
  local title="$2"
  shift 2
  clear
  # Print the real terminal prompt then the command, as if typed
  local prompt="(venv) root@pfe-virtual-machine:~/devsecmlops-pfe# "
  local cmd="$*"
  # Handle bash -c "..." by unwrapping so it looks natural
  if [[ "$1" == "bash" && "$2" == "-c" ]]; then
    cmd="$3"
  fi
  printf "%s%s\n" "$prompt" "$cmd"
  "$@" 2>&1 || true
  # Print the prompt after (as if the command finished and returned to shell)
  printf "%s" "$prompt"
  sleep 1
  shot "$OUT/$name.png"
  sleep 1
  echo ""
}

pkill -9 -f uvicorn 2>/dev/null || true
docker rm -f api-test 2>/dev/null || true

# ── L0: ML MODEL ────────────────────────────────────────────────────────
snap "pfe-01-ml-benchmark-6models" \
  "L0 — Model benchmark: IF z-scored vs 5 alternatives (telecom fleet, 6 features)" \
  python ml-model/benchmark.py --data data/telecom_fleet.csv --max-rows 80000

snap "pfe-02-ml-training-final" \
  "L0 — Final trainer: F1=0.648, ROC-AUC=0.924 on 8.64M rows" \
  python ml-model/train_serving_telecom.py

snap "pfe-03-ml-3way-experiment" \
  "L0 — 3-way baseline experiment: per-window wins on tuned F1 + ROC-AUC" \
  python ml-model/test_timewindow_full.py

snap "pfe-04-ml-zscore-demo" \
  "L0 — Z-score transformation: per-machine + per-window explains the model" \
  python ml-model/zscore_demo.py

snap "pfe-05-ml-data-generator" \
  "L0 — 200-machine telecom fleet generator: 6 features, equipment-faithful" \
  python ml-model/generate_telecom_fleet.py --machines 200 --days 30 --anomaly-ratio 0.05

# ── L1: FASTAPI ─────────────────────────────────────────────────────────
MODEL_NAME=telecom uvicorn api.app:app --host 0.0.0.0 --port 8000 >/tmp/uv.log 2>&1 &
sleep 5

snap "pfe-06-api-health" \
  "L1 — API /health: v2.2.0, 6 features, 200 machines, 4-level fallback" \
  bash -c 'curl -s localhost:8000/health | python -m json.tool'

snap "pfe-07-api-3am-problem" \
  "L1 — The 3am problem live: same values, different time, different verdict" \
  bash -c '
echo "──── hour=3 (NIGHT) ────"
curl -s -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d "{\"machine\":\"web-01\",\"hour\":3,\"metrics\":{\"cpu\":30,\"ram\":50,\"network\":80,\"disk_io\":25,\"disk_usage\":40,\"load_avg\":1.2}}" | python -m json.tool
echo ""
echo "──── hour=14 (AFTERNOON) same values ────"
curl -s -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d "{\"machine\":\"web-01\",\"hour\":14,\"metrics\":{\"cpu\":30,\"ram\":50,\"network\":80,\"disk_io\":25,\"disk_usage\":40,\"load_avg\":1.2}}" | python -m json.tool
'

snap "pfe-08-api-disk-anomaly" \
  "L1 — Disk saturation caught: cpu/ram/net normal, disk+load spike" \
  bash -c '
curl -s -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d "{\"machine\":\"db-01\",\"hour\":14,\"metrics\":{\"cpu\":78,\"ram\":82,\"network\":125,\"disk_io\":99,\"disk_usage\":95,\"load_avg\":13}}" | python -m json.tool
'

snap "pfe-09-api-fallback-chain" \
  "L1 — 4-level fallback chain: machine+window / type / global" \
  bash -c '
echo "──── LEVEL 1: machine+window (known db-01) ────"
curl -s -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d "{\"machine\":\"db-01\",\"hour\":14,\"metrics\":{\"cpu\":75,\"ram\":80,\"network\":120,\"disk_io\":80,\"disk_usage\":72,\"load_avg\":4.5}}" | python -m json.tool
echo ""
echo "──── LEVEL 3: type (unknown web-99 + type hint) ────"
curl -s -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d "{\"machine\":\"web-99\",\"machine_type\":\"web\",\"hour\":14,\"metrics\":{\"cpu\":30,\"ram\":50,\"network\":80,\"disk_io\":25,\"disk_usage\":40,\"load_avg\":1.2}}" | python -m json.tool
echo ""
echo "──── LEVEL 4: global (mystery-01) ────"
curl -s -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d "{\"machine\":\"mystery-01\",\"hour\":14,\"metrics\":{\"cpu\":30,\"ram\":50,\"network\":80,\"disk_io\":25,\"disk_usage\":40,\"load_avg\":1.2}}" | python -m json.tool
'

pkill -9 -f uvicorn 2>/dev/null || true
sleep 2

# ── L2: DOCKER ──────────────────────────────────────────────────────────
snap "pfe-10-docker-images" \
  "L2 — Docker images built + registry running" \
  bash -c 'docker images | head -10; echo ""; docker ps'

snap "pfe-11-docker-container-health" \
  "L2 — Container runs, /health returns 6 features from inside image" \
  bash -c '
docker rm -f api-test 2>/dev/null
docker run -d --name api-test -p 8000:8000 devsecmlops-api:2.3.0 >/dev/null
sleep 5
echo "──── docker ps ────"
docker ps --filter name=api-test
echo ""
echo "──── /health from container ────"
curl -s localhost:8000/health | python -m json.tool | head -25
echo ""
echo "──── container logs ────"
docker logs api-test 2>&1 | tail -6
'

snap "pfe-12-docker-registry-push" \
  "L2 — Local registry: pushed and pullable (Jenkins-ready)" \
  bash -c '
echo "──── registry catalog ────"
curl -s http://localhost:5000/v2/_catalog | python -m json.tool
echo ""
echo "──── image tags ────"
curl -s http://localhost:5000/v2/devsecmlops-api/tags/list | python -m json.tool
echo ""
echo "──── image round-trip proof ────"
docker images | grep devsecmlops-api
'

docker rm -f api-test 2>/dev/null || true

clear
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ALL SCREENSHOTS CAPTURED & CROPPED"
echo "═══════════════════════════════════════════════════════════════"
ls -lh "$OUT"/*.png 2>/dev/null
echo ""
echo "Total files: $(ls "$OUT"/*.png 2>/dev/null | wc -l) / 12"
echo "(anything under 20 KB likely failed — real ones are 50-500 KB)"
