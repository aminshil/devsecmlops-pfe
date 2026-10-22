# Defense demo script

Prepared against build `2.20.3-b112` (all 11 CI stages green, 188 tests, Quality Gate OK; serves the model promoted on 21 October by model build 9: all 17.28M rows plus 315 judged feedback rows, v4 F1 0.7292, threshold 0.91), commit `aaf947ff` or later (`git log --oneline -5`). About 15 minutes.

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
| 0 | 0:30 | Jenkins | open `devsecmlops-pipeline` → the last green build (#112). **Do not start a build**: #98 took 46 minutes on a base-image cache miss | "This is the last build: 11 stages, all green. I'll walk through it at step 12." | Log (b112) |
| 1 | 1 | Control panel → **Status** | open the tab | "Seven layers, eleven live checks: API, registry, Jenkins, SonarQube, MLflow, Kubernetes, Prometheus, Grafana, exporters." Expect 11 of 11 up. The model card shows the selector name `telecom_v3`; the model that answers is chosen per request (step 4). | Log (rehearsal) |
| 2 | 2 | Terminal | block D below (`/health` with the long machine list collapsed, so the lineage is visible) | "The deployed service reports its own lineage: version, the served model's F1 and the artifact hashes it verified before loading." | Log (rehearsal) |
| 3 | 2 | Terminal | the in-pod smoke test (block A below) | "This is the exact check CI runs after every deploy: a request without history is answered by v3, one with a 10-reading history by v4." Expect 7 `PASS` lines. | Log (stage 9) |
| 4 | 1 | Terminal | the model-count snippet (block B below) | "Almost all traffic is v4. v3 answers only a machine's first 9 requests after the traffic generator starts or wraps (no 10-reading history yet) and any request sent without history." Expect both keys, v4 far larger. | Log (rehearsal) |
| 5 | 2 | Control panel → **Operator** | preset `memory-leak`, run | "A prediction with per-feature z-scores, the likely cause, and each model's vote. The z-scores say which metric drove the alert." | Rehearse |
| 6 | 2 | Control panel → **Demo** | send a batch | "Real held-out readings, scored live, with true and false positives shown. About a third of alerts are false: precision is 0.67, and I show that rather than hide it." | Rehearse |
| 7 | 1 | Control panel → **Root Cause** | inject a router cascade | "One router failing makes its machines alert too; the graph ranks the router as the root cause and the rest as downstream effects." | Rehearse |
| 8 | 2 | Grafana | live predictions, model served per pod, top-25 anomaly probability | "25 panels in 5 rows, provisioned from the repo, so the dashboard cannot drift from git. Prometheus scrapes each pod separately, because scraping the NodePort mixed replicas." In the top-25 table the red text is the model's likely cause for each current anomaly, the green bar is the classifier probability; `safety-net only` means the IsolationForest flagged it with a low classifier score. The traffic is a replay of the independent test set. | Log (rehearsal) |
| 9 | 1 | Terminal | block C below | "Pods run non-root, read-only filesystem, all capabilities dropped. The autoscaler is 2 to 5 replicas at 70% CPU." | Log (rehearsal) |
| 10 | 2 | Terminal | `python3 scripts/verify_model_manifest.py --min-f1 0.60` | "The CI model gate: artifact hashes, recorded F1, and that no deployment file carries a threshold: it is read from the manifest, where it was selected on an independent validation fleet." | Log |
| 11 | 1 | Terminal (optional) | block E below | "I change the model selector to the legacy value and the gate fails." Always restore with `git checkout`. | Log |
| 12 | 2 | Jenkins | back to the running build, or the last green one | Point at stage 1b (188 tests in b112; 192 on main with the alerting checks), stage 2b (Quality Gate), stage 3 (gate lines), stage 6 (`0` vulnerabilities), stage 9 (smoke test). Then the README run-history table: runs 1, 2, 9 and 10 are builds the gates stopped. | Log |
| 13 | 1 | MLflow, SonarQube | open each | "Every pipeline training run is logged here, with artifacts stored in MinIO. SonarQube shows the blocking quality gate." | Rehearse |
| 14 | 1 | README | "At a glance" and "Known limitations" | "Every number here traces to the manifest or a log. These are the limits I know about." | n/a |

### The feedback loop and retraining (about 5 minutes, after step 7)

**Precondition.** The cluster runs the latest image (history logging; `kubectl -n ml-serving get deploy -o jsonpath='{.items[*].spec.template.spec.containers[*].image}'` should show `2.20.3-b112`). The feedback table lives on a 2 Gi volume and survives reboots (verified across two reboots; the Continuous training tab shows the live counts). Only a deleted volume empties it.

1. **Control panel → Demo → Send 20 → Review alerts only.** Say: "the readings come from a labelled fleet that is not part of training, validation or test; the cluster pods answer and log them; the verdicts come from the generator's ground truth, standing in for an operator."
1b. **Review tab (optional, 30 seconds):** the anomalies the pods flagged that nobody has judged. Press Real or False alarm on a row; it becomes training data for the next retraining run. Judge only rows recorded by the Demo tab, never rows recorded by the production agent: those are test-set readings. Say: "this is how an operator feeds the loop without opening Postgres."
1c. **Fleet tab (optional, 1 minute):** every router and its dependents, coloured by the pods' verdicts. Say: "a router card is judged from its whole group: machines that stop reporting behind it mean it is down, many degraded with the router looking normal means a suspected gray failure." Be ready for: the replayed v2 fleet rarely lights a router up (Decision 9b); the rule is measured on the realistic v3 fleets in README 9c.
1d. **Alerting (optional, 3 minutes, rehearse first):** `docker stop grafana`, keep talking; about 2 minutes later Telegram shows "LayerDown · Grafana is down"; then `docker start grafana` and the ✅ resolved message follows. Say: "Prometheus decides, Alertmanager delivers; the alert fires even with this panel closed. The rules are unit-tested with promtool." Machine anomalies arrive as batched `AnomalyDetected` messages.
2. **Continuous training tab:** predictions logged, judged and not judged, the model the cluster serves, the retraining history. The history table lists runs recorded in the repo manifest: row 1 is the 21 October promotion (promoted true, MLflow `d81b7ea6…`, 315 feedback rows, threshold 0.91), then the 19 October promotion (MLflow `241447d0…`, all 17.28M rows), then the 13 October promotion (MLflow `f622a7a9…`, threshold 0.6), then the 30 September full run (not promoted) and, if listed, an early 22 July attempt the guardrail rejected (F1 0.718 to 0.709, 500 feedback rows). The 2 October full run (MLflow `57e33ba0…`) is in Jenkins and MLflow only. The cluster's model card now shows the promotion time and the MLflow run.
3. **Jenkins → `devsecmlops-model` → the last full run's console** (do not start a full run live: about 20 minutes and 9+ GB of free memory). Point at stage 2 (judged rows exported), stage 4 (rows used and skipped, candidate against production F1, the guardrail), and stage 6 (the MLflow run id and six model files read back from MinIO).
4. **MLflow:** open run `d81b7ea68cb84ca68c6f485676d2ecf5` in experiment `telecom-production-model`: the 21 October run that was promoted and is now served (v4 F1 0.7292, P 0.6713, R 0.7979; v3 0.6667; threshold 0.91; 315 feedback rows). The 19 October run `241447d0…` (0.7291) is the previous production model and the 13 October run `f622a7a9…` (0.7278) the one before. The 2 October run `57e33ba05b44` (0.7280) and the 30 September run `1cc2b5a0fa75` (0.7278) are earlier full runs. The rest are small smoke runs (v4 F1 about 0.65); do not open them.

Say it plainly: the 21 October run is the whole loop working: 315 operator verdicts exported from PostgreSQL (222 used for v4), a retrain on all 17.28M rows, the threshold re-selected by the pipeline (0.91 again), the guardrail passed, the model files checked in MLflow and MinIO, then committed and deployed by the pipeline with no manual step. F1 moved from 0.7291 to 0.7292: within noise, and 222 rows among 17.28M could not do more, so I claim no improvement. The live cluster validation (16,600 requests, 0 errors, F1 0.7755 on a 14-day sample) was run on the 19 October model and not repeated. The verdicts are simulated. Proven: Log (the full runs, live counts).

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

- **What the scores are measured on.** Precision 0.6713, recall 0.7979, F1 0.7292 (the model served since 21 October) are on an independent test fleet (seed 123) from the project's synthetic generator, with the generator's labels; the threshold was chosen on a separate validation fleet (seed 7). Operator verdicts in the feedback loop are simulated.
- **Live validation (19 October model; the 21 October retrain differs offline by +0.0001 and was not revalidated live).** 16,600 requests through the NodePort, all answered by v4, 0 errors, threshold 0.91 read from the manifest: F1 0.7755, precision 0.7202, recall 0.84. This sample (first 14 days, one reading in 480 per machine (every 4 hours at 30-second resolution)) differs from the full test file, so do not present it as better than the full-file F1.
- **Real data.** The only real-data check is SMD: F1 0.269 per machine and window, on 37 features, outside the production path (`ml-model/load_smd.py`). Say so before being asked.
- **Router failures (README 9c, full-size v3 fleets).** *15 machines anomalous and the router normal?* Only in the recovery tail after the router's incident (449 of 449 cases, 0 unexplained); live, that picture means a gray failure. *Every machine anomalous?* 96% of such moments are a hard-down router, 4% a severe gray failure; the router is anomalous in both. *Router anomalous, machines fine?* A control-plane event, or a gray failure in its first minutes. *Why not adopt v3?* A model trained on it measured worse (F1 0.7901 vs 0.8027, less precise at every recall up to 0.88) and the guardrail rejected it. *Gray failures?* Hard-down routers are all named within 30 s; gray ones 5% of the time, a feature limit (no retransmission signal), stated in Known limitations.
- **Feedback and the test set.** In the demo the traffic agent replays the test fleet, so judging its rows in Review would leak test readings into training. Measured: build 9 used 48 such rows, 0.0003% of the test set, no effect on the score. In the production profile that agent is off.
- **Can feedback teach the model to catch more?** Barely: Review lists only flagged readings, so verdicts confirm alerts or teach the model to flag less; missed anomalies never reach Review.
- **Who stops a bad model being committed by hand?** Process: only `Jenkinsfile.model` commits models. The CI gate proves the artifacts, threshold and evaluation are consistent, not that the guardrail approved them (planned check, in Known limitations). The F1 floor of 0.60 is a backstop; the guardrail is the real check, relative to the current model.
- **Why does v3 use 0.91?** It inherits the v4 threshold: v3 is the fallback for requests without history, F1 0.6667, recall 0.7545. Demo without history and you are showing v3.
- **Which model is live after a recovery?** `model_lineage.mlflow_run_id` on `/health`; the version string is the same for every build.
- **Is the hash check protection against an attacker?** Against an accidental or partial swap, yes; not against someone who can rewrite the manifest too.
- **Persistent anomalies in Grafana** are real episodes in the data (about 60 readings, up to 116), not stuck gauges; about 13 of 200 machines are in one at any time.

---

## 5. Do not do live

- `train_production.py` of any kind. Training and evaluation use up to about 11 GB of memory, and this VM froze once under that load.
- `PROMOTE=true` on `devsecmlops-model`. It changes the served model, pushes a commit to `main` and starts a deploy build that needs SonarQube running. It has run three times (13 October, build 7; 19 October, build 8, full data; 21 October, build 9, with feedback); the next promotion belongs after the defense.
- Starting a main-pipeline build. Build #98 took 46 minutes on a base-image cache miss (about 600 MB of wheels at under 400 kB/s).
- The Kubernetes **Fix** button in Status (untested live).
- The Ansible `production` profile. It overwrites the PostgreSQL Secret, and the running database keeps its old password.
- Deleting Docker volumes, anything under `data/`, or trimming the registry.
- Restarting Docker. It leaves Minikube's networking stale.

---

## 6. After the defense

- Rotate every credential that appeared in chat or in terminal history (Grafana, ops UI, MinIO).
- Then take up the deferred items in "Known limitations": a dependency lockfile, a NetworkPolicy.
