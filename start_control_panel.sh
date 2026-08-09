#!/usr/bin/env bash
# start_control_panel.sh — launches the DevSecMLOps control panel + dependencies
set -u
cd "$(dirname "$0")"

GREEN='\033[0;32m'; RED='\033[0;31m'; YEL='\033[1;33m'; NC='\033[0m'
ok(){ echo -e "  ${GREEN}✓${NC} $1"; }
warn(){ echo -e "  ${YEL}!${NC} $1"; }
bad(){ echo -e "  ${RED}✗${NC} $1"; }

VM_IP=$(hostname -I | awk '{print $1}')
echo "=============================================="
echo " DevSecMLOps Control Panel — startup"
echo "=============================================="

if [ -d venv ]; then source venv/bin/activate; ok "venv activated"; else bad "venv not found — run from repo root"; exit 1; fi

echo ""; echo "[1/5] Kubernetes (Minikube)"
if minikube status 2>/dev/null | grep -q "host: Running"; then ok "minikube already running"
else warn "minikube not running — starting…"; minikube start --driver=docker --force >/dev/null 2>&1 && ok "minikube started" || warn "minikube start failed — K8s panels empty"; fi

echo ""; echo "[2/5] Support services"
docker start node-exporter grafana 2>/dev/null >/dev/null || true
if ! curl -s -m 3 http://localhost:9100/metrics -o /dev/null 2>/dev/null; then
  docker rm -f node-exporter 2>/dev/null >/dev/null
  docker run -d --name node-exporter --network host -v /:/host:ro,rslave prom/node-exporter:latest --path.rootfs=/host >/dev/null 2>&1 && ok "node-exporter started" || warn "node-exporter not started"
else ok "node-exporter up (:9100)"; fi
curl -s -m 3 http://localhost:3000/api/health -o /dev/null 2>/dev/null && ok "grafana up (:3000)" || warn "grafana not reachable (:3000)"

echo ""; echo "[3/5] Monitoring pipeline"
pgrep -f replay_exporter.py >/dev/null && ok "replay exporter up (:9200)" || { nohup python3 monitoring/replay_exporter.py >/tmp/replay.log 2>&1 & ok "replay exporter started (:9200)"; }
sleep 2
pgrep -f anomaly_bridge.py >/dev/null && ok "anomaly bridge up (:9300)" || { nohup python3 monitoring/anomaly_bridge.py >/tmp/bridge.log 2>&1 & ok "anomaly bridge started (:9300)"; }
pgrep -f k8s_exporter.py >/dev/null && ok "k8s exporter up (:9400)" || { nohup python3 monitoring/k8s_exporter.py >/tmp/k8s_exp.log 2>&1 & ok "k8s exporter started (:9400)"; }
pgrep -f "prometheus --config" >/dev/null && ok "prometheus up (:9090)" || { nohup prometheus --config.file=monitoring/prometheus.yml --storage.tsdb.path=/tmp/prom_data >/tmp/prom.log 2>&1 & ok "prometheus started (:9090)"; }

echo ""; echo "[4/5] Control panel API"
pkill -f "uvicorn.*app:app.*8000" 2>/dev/null; sleep 2
MODEL_NAME=telecom_v3 PREDICT_THRESHOLD=0.85 DATABASE_URL="postgresql://x:x@localhost:9999/n" nohup uvicorn api.app:app --host 0.0.0.0 --port 8000 >/tmp/control_panel.log 2>&1 &
sleep 8
curl -s -m 5 http://localhost:8000/ui -o /dev/null 2>/dev/null && ok "control panel API up (:8000)" || bad "control panel failed — see /tmp/control_panel.log"

echo ""; echo "[5/5] Layer status"
curl -s -m 8 http://localhost:8000/ui/status 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for l in d['layers']:
        print('  '+('\033[0;32m OK\033[0m' if l['up'] else '\033[0;31m DOWN\033[0m')+' '+l['layer']+' '+l['name']+' — '+l['detail'])
    print(); print(f'  {d[\"up\"]}/{d[\"total\"]} layers up')
except Exception: print('  (status not ready yet)')
"
echo ""; echo "=============================================="
echo -e " Control panel:  http://${VM_IP}:8000/ui"
echo -e " Grafana:        http://${VM_IP}:3000"
echo "=============================================="
