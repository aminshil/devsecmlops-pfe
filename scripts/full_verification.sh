#!/usr/bin/env bash
# Complete end-to-end verification of every layer, output saved to a file.

OUT="/tmp/full_verification_$(date +%Y%m%d_%H%M%S).txt"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# The cluster files the playbook and Jenkins use (ansible/group_vars/all.yml). Without them kubectl and
# minikube run as root looked in /root/.kube and /root/.minikube and every L4 check failed (VPS test).
export KUBECONFIG="${KUBECONFIG:-/home/pfe/.kube/config}"
export MINIKUBE_HOME="${MINIKUBE_HOME:-/home/pfe/.minikube}"

{
echo "════════════════════════════════════════════════════════════════"
echo "  DevSecMLOps Platform — Full Verification"
echo "  $(date)"
echo "════════════════════════════════════════════════════════════════"

echo ""
echo "════════════════════ L0/L1: ML MODEL + API ════════════════════"
echo "(host API on :8000, the control panel's process: it has no database, so prediction_id is null;"
echo " the cluster API, which stores predictions, is checked in L4)"
echo "--- /health ---"
curl -s -m 5 http://localhost:8000/health | python3 -m json.tool 2>&1 || echo "API NOT REACHABLE"

echo ""
echo "--- /predict (disk saturation test) ---"
curl -s -X POST http://localhost:8000/predict -H "Content-Type: application/json" \
  -d '{"machine":"db-01","hour":14,"metrics":{"cpu":78,"ram":82,"network":125,"disk_io":99,"disk_usage":95,"load_avg":13}}' \
  | python3 -m json.tool 2>&1

echo ""
echo "--- /root-cause test ---"
curl -s -X POST http://localhost:8000/root-cause -H "Content-Type: application/json" \
  -d '{"anomalies":{"router-01":0.55,"web-01":0.62,"web-09":0.58,"app-01":0.60,"mystery-01":0.71}}' \
  | python3 -m json.tool 2>&1

echo ""
echo "════════════════════ L2: DOCKER + REGISTRY ════════════════════"
echo "--- devsecmlops-api images ---"
docker images devsecmlops-api --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>&1

echo ""
echo "--- Registry catalog ---"
curl -s -m 5 http://localhost:5000/v2/_catalog 2>&1
echo ""
curl -s -m 5 http://localhost:5000/v2/devsecmlops-api/tags/list 2>&1

echo ""
echo "════════════════════ L3: JENKINS + SONARQUBE + REGISTRY ════════════════════"
for name in jenkins sonarqube registry; do
  status=$(docker inspect -f '{{.State.Status}}' $name 2>/dev/null)
  health=$(docker inspect -f '{{.State.Health.Status}}' $name 2>/dev/null)
  echo "$name: status=${status:-NOT FOUND} health=${health:-n/a}"
done

echo ""
echo "--- Jenkins reachable ---"
curl -s -m 5 -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8080 2>&1

echo ""
echo "--- SonarQube reachable ---"
curl -s -m 5 http://localhost:9000/api/system/status 2>&1

echo ""
echo "════════════════════ L4: KUBERNETES ════════════════════"
echo "--- Nodes ---"
timeout 10 kubectl get nodes 2>&1

echo ""
echo "--- Pods (ml-serving) ---"
timeout 10 kubectl get pods -n ml-serving 2>&1

echo ""
echo "--- Service ---"
timeout 10 kubectl get svc -n ml-serving 2>&1

echo ""
echo "--- HPA ---"
timeout 10 kubectl get hpa -n ml-serving 2>&1

echo ""
echo "--- kubectl top (metrics-server) ---"
timeout 10 kubectl top pods -n ml-serving 2>&1
timeout 10 kubectl top nodes 2>&1

echo ""
echo "--- Live endpoint via NodePort ---"
MINIKUBE_IP=$(minikube ip 2>/dev/null)
echo "Minikube IP: $MINIKUBE_IP"
curl -s -m 5 "http://$MINIKUBE_IP:30080/health" 2>&1 | python3 -m json.tool 2>&1

echo ""
echo "════════════════════ L5: MONITORING ════════════════════"
echo "--- Background processes ---"
pgrep -af "node_exporter|prometheus --config|grafana server|production_agent.py|k8s_exporter.py|mlflow server" 2>&1

echo ""
echo "--- Prometheus health ---"
curl -s -m 5 http://localhost:9090/-/healthy 2>&1

echo ""
echo "--- Prometheus targets ---"
curl -s -m 5 http://localhost:9090/api/v1/targets 2>&1 | python3 -c "
import json,sys
d = json.load(sys.stdin)
for t in d['data']['activeTargets']:
    print(t['labels']['job'], '->', t['health'])
" 2>&1

echo ""
echo "--- Grafana health ---"
curl -s -m 5 http://localhost:3000/api/health 2>&1

echo ""
echo "--- Host services (systemd) ---"
for u in prometheus k8s-exporter production-agent control-panel prometheus-watchdog; do
  echo "$u: $(systemctl is-active "$u" 2>&1)"
done

echo ""
echo "--- Machines reporting (production agent -> serving pods -> Prometheus) ---"
# the retired fleet simulator's sim_cpu_percent always read 0 here (VPS test); these are the recording
# rules the Grafana dashboard and the Telegram list use (monitoring/alerting/alert_rules.yml)
for q in 'count(fleet:machine_silent == 0)' 'count(fleet:machine_silent == 1)' 'count(fleet:machine_confirmed:now == 1)'; do
  curl -s -m 5 -G 'http://localhost:9090/api/v1/query' --data-urlencode "query=$q" 2>&1 | python3 -c "
import json,sys
r = json.load(sys.stdin)['data']['result']
print(f'{sys.argv[1]:42} ->', r[0]['value'][1] if r else '0')
" "$q" 2>&1
done

echo ""
echo "--- Alertmanager (Telegram) ---"
curl -s -m 5 -o /dev/null -w "ready: HTTP %{http_code}\n" http://localhost:9093/-/ready 2>&1

echo ""
echo "--- MLflow and MinIO (demo profile) ---"
curl -s -m 5 -o /dev/null -w "MLflow /health: HTTP %{http_code}\n" http://localhost:5001/health 2>&1
echo "MLflow container: $(docker inspect -f '{{.State.Status}} / health {{.State.Health.Status}}' mlflow 2>&1)"
curl -s -m 5 -o /dev/null -w "MinIO /minio/health/live: HTTP %{http_code}\n" http://localhost:9001/minio/health/live 2>&1

echo ""
echo "════════════════════ GIT + VERSION ════════════════════"
cd "$REPO" || exit 1
echo "--- git status ---"
git status --short 2>&1

echo ""
echo "--- VERSION ---"
cat VERSION 2>&1

echo ""
echo "--- Last 5 commits ---"
git log --oneline -5 2>&1

echo ""
echo "--- Last 3 tags ---"
git tag --sort=-v:refname 2>&1 | head -3

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  VERIFICATION COMPLETE"
echo "════════════════════════════════════════════════════════════════"

} > "$OUT" 2>&1

echo "Full verification saved to: $OUT"
echo ""
echo "=== Quick summary (first 5 lines of each section) ==="
grep "^════" "$OUT"
echo ""
echo "To view the full file:"
echo "  cat $OUT"
echo ""
echo "To copy it to your repo for easy access:"
echo "  cp $OUT ~/devsecmlops-pfe/verification_report.txt"
