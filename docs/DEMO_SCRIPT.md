# Defense demo script

Prepared against build `2.20.3-b107` (all 11 CI stages green, 141 tests, Quality Gate OK; serves the model promoted on 19 October, trained on all 17.28M rows, v4 F1 0.7291, threshold 0.91), commit `0ea14df3` (`git log --oneline -5`). About 15 minutes.

**How to read the "Proven" column.** *Log* means the exact command or output appeared in a real Jenkins or terminal log during this project. *Rehearse* means it is documented in the README but was not run today. Run every *Rehearse* step once before the defense, from a cold start.

Replace `<vm-ip>` with the output of `hostname -I`. Type credentials before the audience arrives and do not show them on screen.

---

## 1. T-30 minutes: checklist

```bash
cd ~/devsecmlops-pfe
df -h / | tail -1                                           # want < 85% (it was 97% once; see Fallbacks)
systemctl is-active prometheus k8s-exporter production-agent control-panel mlflow   # 5 x active
docker ps --format '{{.Names}}: {{.Status}}' | sort         # 7 Up: grafana jenkins minikube minio node-exporter registry sonarqube
kubectl -n ml-serving get pods                              # 2 x anomaly-api + postgres-0, all Running
docker exec jenkins getent hosts sonarqube registry minio   # 3 addresses: devsecmlops-net is intact
python3 scripts/verify_model_manifest.py --min-f1 0.60      # all OK lines
git status --short; git log --oneline -1                    # clean tree
```

Open these tabs in advance and log in:

| Tab | URL |
|---|---|
| Control panel | `http://<vm-ip>:8000/ui` (basic auth) |
| Grafana | `http://<vm-ip>:3000` → dashboard `devsecmlops-fleet` |
| Jenkins | `http://<vm-ip>:8080` → job `devsecmlops-pipeline` |
| SonarQube | `http://<vm-ip>:9000` → project `devsecmlops-pfe` |
| MLflow | `http://<vm-ip>:5001` → experiment `telecom-production-model` |
| README | the GitHub page, scrolled to "At a glance" |

Have `screenshots/` open as a last-resort fallback.

---

## 2. The flow

| # | Min | Show | Command or action | What to say | Proven |
|---|---|---|---|---|---|
| 0 | 0:30 | Jenkins | open `devsecmlops-pipeline` → the last green build (#107). **Do not start a build**: #98 took 46 minutes on a base-image cache miss | "This is the last build: 11 stages, all green. I'll walk through it at step 12." | Log (b107) |
| 1 | 1 | Control panel → **Status** | open the tab | "Seven layers, each with a live check: API, registry, Jenkins, SonarQube, MLflow, Kubernetes, Prometheus, Grafana, exporters." Expect 11 of 11 up. The model card shows the selector name `telecom_v3`; the model that answers is chosen per request (step 4). | Log (rehearsal) |
| 2 | 2 | Terminal | block D below (`/health` with the long machine list collapsed, so the lineage is visible) | "The deployed service reports its own lineage: version, the served model's F1 and the artifact hashes it verified before loading." | Log (rehearsal) |
| 3 | 2 | Terminal | the in-pod smoke test (block A below) | "This is the exact check CI runs after every deploy: a request without history is answered by v3, one with a 10-reading history by v4." Expect 6 `PASS` lines. | Log (stage 9) |
| 4 | 1 | Terminal | the model-count snippet (block B below) | "Almost all traffic is v4. v3 answers only a machine's first 9 requests after the traffic generator starts or wraps (no 10-reading history yet) and any request sent without history." Expect both keys, v4 far larger. | Log (rehearsal) |
| 5 | 2 | Control panel → **Operator** | preset `memory-leak`, run | "A prediction with per-feature z-scores, the likely cause, and each model's vote. The z-scores say which metric drove the alert." | Rehearse |
| 6 | 2 | Control panel → **Demo** | send a batch | "Real held-out readings, scored live, with true and false positives shown. About a third of alerts are false: precision is 0.66, and I show that rather than hide it." | Rehearse |
| 7 | 1 | Control panel → **Root Cause** | inject a router cascade | "One router failing makes its machines alert too; the graph ranks the router as the root cause and the rest as downstream effects." | Rehearse |
| 8 | 2 | Grafana | live predictions, model served per pod, top-25 anomaly probability | "25 panels in 5 rows, provisioned from the repo, so the dashboard cannot drift from git. Prometheus scrapes each pod separately, because scraping the NodePort mixed replicas." In the top-25 table the red text is the model's likely cause for each current anomaly, the green bar is the classifier probability; `safety-net only` means the IsolationForest flagged it with a low classifier score. The traffic is a replay of the independent test set. | Log (rehearsal) |
| 9 | 1 | Terminal | block C below | "Pods run non-root, read-only filesystem, all capabilities dropped. The autoscaler is 2 to 5 replicas at 70% CPU." | Log (rehearsal) |
| 10 | 2 | Terminal | `python3 scripts/verify_model_manifest.py --min-f1 0.60` | "The CI model gate: artifact hashes, recorded F1, and that no deployment file carries a threshold: it is read from the manifest, where it was selected on an independent validation fleet." | Log |
| 11 | 1 | Terminal (optional) | block E below | "I change the model selector to the legacy value and the gate fails." Always restore with `git checkout`. | Log |
| 12 | 2 | Jenkins | back to the running build, or the last green one | Point at stage 1b (141 tests), stage 2b (Quality Gate), stage 3 (gate lines), stage 6 (`0` vulnerabilities), stage 9 (smoke test). Then the README run-history table: runs 1, 2, 9 and 10 are builds the gates stopped. | Log |
| 13 | 1 | MLflow, SonarQube | open each | "Every pipeline training run is logged here, with artifacts stored in MinIO. SonarQube shows the blocking quality gate." | Rehearse |
| 14 | 1 | README | "At a glance" and "Known limitations" | "Every number here traces to the manifest or a log. These are the limits I know about." | n/a |

### The feedback loop and retraining (about 5 minutes, after step 7)

**Precondition.** The cluster runs the latest image (history logging; `kubectl -n ml-serving get deploy -o jsonpath='{.items[*].spec.template.spec.containers[*].image}'` should show `2.20.3-b99` or later). The feedback table lives on a 2 Gi volume and survives reboots (verified across two reboots; the Continuous training tab shows the live counts). Only a deleted volume empties it.

1. **Control panel → Demo → Send 20 → Review alerts only.** Say: "the readings come from a labelled fleet that is not part of training, validation or test; the cluster pods answer and log them; the verdicts come from the generator's ground truth, standing in for an operator."
1b. **Review tab (optional, 30 seconds):** the anomalies the pods flagged that nobody has judged. Press Real or False alarm on a row; it becomes training data for the next retraining run. Say: "this is how an operator feeds the loop without opening Postgres."
2. **Continuous training tab:** predictions logged, judged and not judged, the model the cluster serves, the retraining history. The history table lists runs recorded in the repo manifest: row 1 is the 19 October promotion (promoted true, MLflow `241447d0…`, all 17.28M rows, threshold 0.91), then the 13 October promotion (MLflow `f622a7a9…`, threshold 0.6), then the 30 September full run (not promoted) and, if listed, an early 22 July attempt the guardrail rejected (F1 0.718 to 0.709, 500 feedback rows). The 2 October full run (MLflow `57e33ba0…`) is in Jenkins and MLflow only. The cluster's model card now shows the promotion time and the MLflow run.
3. **Jenkins → `devsecmlops-model` → the last full run's console** (do not start a full run live: about 20 minutes and 9+ GB of free memory). Point at stage 2 (judged rows exported), stage 4 (rows used and skipped, candidate against production F1, the guardrail), and stage 6 (the MLflow run id and six model files read back from MinIO).
4. **MLflow:** open run `241447d01be24253a92874716dbfdf18` in experiment `telecom-production-model`: the 19 October run that was promoted and is now served (v4 F1 0.7291, P 0.6713, R 0.7979; v3 0.6664; threshold 0.91). The 13 October run `f622a7a9…` (0.7278) is the previous production model. The 2 October run `57e33ba05b44` (0.7280) and the 30 September run `1cc2b5a0fa75` (0.7278) are earlier full runs. The other 12 runs in the experiment are small smoke runs (v4 F1 about 0.65); do not open them.

Say it plainly: the 19 October run trained on all 17.28M rows and exported 262 judged rows, 214 used for v4, so feedback did not drive the result. F1 went from 0.7278 to 0.7291 and recall from 0.7941 to 0.7979: within noise, so I claim no improvement. What changed is the method: the pipeline selected the threshold itself (0.91, the highest precision with recall of at least 0.80), the API reads it from the manifest, and no file is edited by hand. The guardrail passed, the model was promoted and deployed by the pipeline, and the live cluster validation was rerun on it (16,600 requests, 0 errors, F1 0.7755 on a 14-day sample, which is not comparable with the full-file 0.7291). The verdicts are simulated. Proven: Log (smoke runs, the full run, live counts).

### Blocks

**A. In-pod smoke test (what CI runs in stage 9)**

```bash
cd ~/devsecmlops-pfe
kubectl exec -i -n ml-serving deploy/anomaly-api -c api -- \
  python3 - --url http://localhost:8000 --expect-version "$(cat VERSION)" --wait 60 < scripts/smoke_test.py
```

**B. Which model answered, per pod**

```bash
for p in $(kubectl -n ml-serving get pods -l app=anomaly-api -o name); do
  kubectl -n ml-serving exec "$p" -c api -- python3 -c "
import urllib.request, re, collections
t = collections.Counter()
for l in urllib.request.urlopen('http://localhost:8000/metrics').read().decode().splitlines():
    if l.startswith('api_predictions_total{'):
        t[re.search(r'model=\"([^\"]+)\"', l).group(1)] += float(l.rsplit(' ', 1)[1])
print(dict(t))"
done
```

Expected: `telecom_v4_rolling` far larger than `telecom_v3` on each pod. The small v3 count is the first 9 requests per machine after the generator (re)starts, plus requests without history.

**C. Kubernetes**

```bash
kubectl -n ml-serving get pods
kubectl -n ml-serving get hpa
kubectl -n ml-serving get deploy anomaly-api -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n ml-serving get deploy anomaly-api -o jsonpath='{.spec.template.spec.containers[0].securityContext}{"\n"}'
```

**D. `/health` with the lineage visible**

```bash
curl -s http://192.168.49.2:30080/health | python3 -c "
import sys, json
def f(x):
    if isinstance(x, dict): return {k: f(v) for k, v in x.items()}
    if isinstance(x, list) and len(x) > 10: return f'[{len(x)} items]'
    return x
print(json.dumps(f(json.load(sys.stdin)), indent=2))"
```

**E. Gate failure on purpose (always restore)**

```bash
sed -i 's/value: "telecom_v3"/value: "telecom_v2"/' kubernetes/deployment.yaml
python3 scripts/verify_model_manifest.py --min-f1 0.60; echo "exit=$?"   # FAIL: model selector drift, exit=1
git checkout kubernetes/deployment.yaml
git status --short                                                       # must be empty
```

---

## 3. If something breaks

| Symptom | Fix | Proven |
|---|---|---|
| Jenkins stage 2: `sonarqube: Name or service not known` | `docker network connect devsecmlops-net sonarqube` (same for `registry`, `minio`); check with `docker exec jenkins getent hosts sonarqube` | Log |
| SonarQube task `FAILED`, Elasticsearch read-only | the disk is above 95%: prune, then `docker restart sonarqube` and wait 60 s | Log |
| Disk above 85% | `docker image prune -a -f --filter "until=24h"; docker builder prune -af; sudo journalctl --vacuum-time=3d` | Log |
| `kubectl` says the server is unreachable after a reboot | normally `minikube-start.service` brings it back within 2 to 3 minutes (`systemctl status minikube-start`). If not: `minikube start --driver=docker --force`, wait, then `scripts/k8s_recover.sh` if pods are still not Ready. Cause and fix: `docs/MINIKUBE_RELIABILITY.md`. Do not use the panel's Kubernetes **Fix** button: `k8s_recover.sh` has not been tested live | Log (reboots) |
| Pods not Ready after a reboot | wait 3 to 5 minutes; `kubectl -n ml-serving get pods` | Log |
| `192.168.49.2:30080` unreachable | `kubectl -n ml-serving port-forward deploy/anomaly-api 8001:8000`, then use `localhost:8001` | Rehearse |
| Anything else | switch to `screenshots/` and the README; do not debug in front of the audience | n/a |

---

## 4. If asked about the numbers

- **What the scores are measured on.** Precision 0.6713, recall 0.7979, F1 0.7291 (the model served since 19 October) are on an independent test fleet (seed 123) from the project's synthetic generator, with the generator's labels; the threshold was chosen on a separate validation fleet (seed 7). Operator verdicts in the feedback loop are simulated.
- **Live validation on the current model (19 October).** 16,600 requests through the NodePort, all answered by v4, 0 errors, threshold 0.91 read from the manifest: F1 0.7755, precision 0.7202, recall 0.84. This sample (first 14 days, one reading in 480 per machine (every 4 hours at 30-second resolution)) differs from the full test file, so do not present it as better than 0.7291.
- **Live agreed with offline (measured on the July model, before the 13 October promotion; not repeated for the new one).** Over 39,789 matched live predictions the cluster scored P 0.676, R 0.788, F1 0.728, against 0.664, 0.797 and 0.725 offline. The comparison script `live_vs_truth.py` is not in the repo unless you committed it; rehearse before using it.
- **Real data.** The only real-data check is SMD: F1 0.269 per machine and window, on 37 features, outside the production path (`ml-model/load_smd.py`). Say so before being asked.
- **Persistent anomalies in Grafana** are real episodes in the data (about 60 readings, up to 116), not stuck gauges; about 13 of 200 machines are in one at any time.

---

## 5. Do not do live

- `train_production.py` of any kind. Training and evaluation use up to about 11 GB of memory, and this VM froze once under that load.
- `PROMOTE=true` on `devsecmlops-model`. It changes the served model, pushes a commit to `main` and starts a deploy build that needs SonarQube running. It has run twice (13 October, build 7; 19 October, build 8, full data); the next promotion belongs after the defense.
- Starting a main-pipeline build. Build #98 took 46 minutes on a base-image cache miss (about 600 MB of wheels at under 400 kB/s).
- The Kubernetes **Fix** button in Status (untested live).
- The Ansible `production` profile. It overwrites the PostgreSQL Secret, and the running database keeps its old password.
- Deleting Docker volumes, anything under `data/`, or trimming the registry.
- Restarting Docker. It leaves Minikube's networking stale.

---

## 6. After the defense

- Rotate every credential that appeared in chat or in terminal history (Grafana, ops UI, MinIO).
- Then take up the deferred items in "Known limitations": a dependency lockfile, a NetworkPolicy.
