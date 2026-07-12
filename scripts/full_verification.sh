#!/usr/bin/env bash
# Complete end-to-end verification of every layer, output saved to a file.

OUT="/tmp/full_verification_$(date +%Y%m%d_%H%M%S).txt"

{
echo "════════════════════════════════════════════════════════════════"
echo "  DevSecMLOps Platform — Full Verification"
echo "  $(date)"
echo "════════════════════════════════════════════════════════════════"

echo ""
echo "════════════════════ L0/L1: ML MODEL + API ════════════════════"
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
curl -s -m 5 http://$MINIKUBE_IP:30080/health 2>&1 | python3 -m json.tool 2>&1

echo ""
echo "════════════════════ L5: MONITORING ════════════════════"
echo "--- Background processes ---"
ps aux | grep -E "node_exporter|prometheus --config|fleet_simulator|anomaly_bridge|grafana server" | grep -v grep 2>&1

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
echo "--- Fleet simulator: machine count ---"
curl -s -m 5 'http://localhost:9090/api/v1/query?query=sim_cpu_percent' 2>&1 | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('machines found:', len(d['data']['result']))
" 2>&1

echo ""
echo "════════════════════ GIT + VERSION ════════════════════"
cd ~/devsecmlops-pfe
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
