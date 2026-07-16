# DevSecMLOps Platform

**Conception et mise en œuvre d'une plateforme DevSecMLOps Cloud-Native**
PFE ESPRIT × Tunisie Telecom — 2025–2026 — Amine Shil

A self-hosted, cloud-native platform that detects anomalies on IT infrastructure
(CPU, RAM, network, disk, load) using machine learning, delivered through a
fully automated, security-gated CI/CD pipeline. Includes dependency-graph-based
root cause analysis to distinguish a true root cause from downstream cascading
victims when multiple machines alert at once.

---

## Status

| Layer | Component | Status |
|-------|-----------|--------|
| L0 | ML model — Isolation Forest, per-machine per-time-window z-score | ✅ Done |
| L1 | FastAPI serving layer + root cause analysis endpoint | ✅ Done |
| L2 | Docker + local registry | ✅ Done |
| L3 | Jenkins CI/CD (7 stages, real SonarQube SAST, Trivy) | ✅ Done |
| L4 | Kubernetes (Minikube) | ✅ Done |
| L5 | Monitoring (Prometheus, Grafana, real-data replay, K8s monitoring) | ✅ Done |
| L6 | Ansible (infrastructure as code) | 📋 Planned |

**Current model:** F1 = 0.663 · Precision = 0.660 · Recall = 0.667 · ROC-AUC = 0.926
on a 200-machine synthetic Tunisie Telecom fleet, 6.75% anomaly rate, 6 features,
30-second sampling resolution.

**Full version history:** see [GitHub Releases](https://github.com/aminshil/devsecmlops-pfe/releases)
— tagged releases from v0.1.0 (initial POC) through v2.8.0, each with
detailed release notes covering what changed and why.

---

## Architecture

```
Git push → Jenkins (SonarQube SAST → Docker build → Trivy scan → registry push)
                                                          │
                                                          ▼
                                          Docker image (model baked in)
                                                          │
                                                          ▼
                                   Kubernetes: FastAPI pods (2+ replicas)
                                                          │
                        ┌─────────────────────────────────┼─────────────────────┐
                        ▼                                 ▼                     ▼
              Node Exporter (VM)                  Real-data replay      Kubernetes exporter
                        │                          (200 machines)                │
                        └─────────────────────────────────┼─────────────────────┘
                                                          ▼
                                                Prometheus scrape
                                                          │
                        ┌─────────────────────────────────┼─────────────────────┐
                        ▼                                                       ▼
              Grafana dashboards (20 panels)          Anomaly bridge → /predict → /root-cause
```

Everything runs on a single self-hosted VM (Ubuntu 22.04) for the PFE demo. The
same Docker images deploy unmodified to a multi-node production cluster — only
configuration (IPs, credentials, registry URL) changes, not code.

### How the model works, in two phases

**Training (offline, once, ~4 minutes):** raw fleet data flows into baseline
construction (mean/std per machine and time window), gets z-scored using those
baselines, and the Isolation Forest fits on the z-scored data. Output: two
frozen files, `telecom_serving_model.pkl` and `telecom_serving_baselines.json`.

**Serving (live, every API call, milliseconds):** an incoming reading is
z-scored using the SAME saved baseline from training (never recomputed live),
fed into the frozen model, and returns `is_anomaly` plus a continuous score
plus per-feature z-scores. If several machines flag as anomalous around the
same time, their results can be batched into `/root-cause`, which uses the
dependency graph to rank them by root-cause likelihood.

Baselines and z-scores are not the same thing: the baseline is two saved
numbers (mean, std) per machine and time window. The z-score is a calculation
done fresh every time using that baseline — `(raw - mean) / std` — both
during training and during every prediction.

---

## The ML model

### Why Isolation Forest

Six anomaly-detection methods were benchmarked on the same data, matched to
production settings (`contamination=0.068`, `n_estimators=200`)
(`ml-model/benchmark.py`, results in `models/results/`):

| Model | Precision | Recall | F1 | F1@adj | ROC-AUC | PR-AUC@adj |
|-------|-----------|--------|-----|--------|---------|------------|
| z-threshold (\|z\|>3) | 0.695 | 0.623 | **0.657** | **0.680** | 0.914 | 0.710 |
| Isolation Forest, raw (no z-score) | 0.362 | 0.359 | 0.361 | 0.382 | 0.771 | 0.409 |
| **Isolation Forest, z-scored (shipped)** | 0.599 | 0.656 | 0.626 | 0.647 | **0.922** | **0.722** |
| OneClassSVM, z-scored | 0.493 | 0.622 | 0.550 | 0.569 | 0.897 | 0.635 |
| Local Outlier Factor, z-scored | 0.245 | 0.332 | 0.282 | 0.303 | 0.735 | 0.292 |
| Autoencoder, z-scored | 0.270 | 0.271 | 0.270 | 0.295 | 0.740 | 0.262 |

F1@adj = point-adjusted F1 (OmniAnomaly/SMD standard): a single correctly
flagged point inside an anomaly block counts the whole block as detected —
the metric commonly reported in time-series anomaly detection research.

Honest reading of this table: the simple z-threshold rule is competitive on
strict F1, and even modestly ahead of Isolation Forest (0.657 vs 0.626). We
still ship Isolation Forest for three reasons a single F1 number does not
capture:

1. **Better ranking quality.** ROC-AUC (0.922 vs 0.914) and PR-AUC@adj
   (0.722 vs 0.710) both favor Isolation Forest — it ranks anomalies more
   reliably across all possible thresholds, not just the one z-threshold
   happens to use.
2. **Continuous, tunable score.** z-threshold is a fixed per-feature cutoff
   (exactly 3 sigma). Isolation Forest exposes a continuous `anomaly_score`
   (already in the API response) that operators can re-tune via
   `contamination` without code changes.
3. **Multivariate detection.** z-threshold flags a reading if ANY single
   feature exceeds 3 sigma. It cannot catch a case where four features are
   each moderately elevated but jointly represent a real incident — a
   structural gap the per-feature rule cannot close.

Isolation Forest also requires no labeled anomalies (production has none),
infers in under 10ms, needs only one key hyperparameter (`contamination`),
and scales cleanly to 200+ machines. Comparing "raw" vs "z-scored" in the
table shows the z-score normalization step is what actually drives its
performance — raw scores 0.361 F1, z-scored reaches 0.626.

### Why unsupervised, not supervised

A supervised classifier (Random Forest, XGBoost, etc.) would score higher F1
on this labeled synthetic dataset, but it needs labeled anomalies to train.
Production infrastructure has none — nobody manually labels every anomalous
minute across 200 servers. Isolation Forest learns "normal" from the incoming
metric stream with zero labels, and can flag anomaly *types* it has never
seen before. The dataset's `label` column is used only to evaluate the model,
never to train it.

### Why per-machine, per-time-window baselines

Every metric is z-scored *before* reaching the model:

```
z = (raw_value - machine's own mean for this time window) / machine's own std
```

A web server idling at 30% CPU and a database server running hot at 75% CPU
are both "normal" — a single global threshold can't capture that, but a
per-machine baseline can.

Baselines are split into four **time windows** — night (00–06), morning
(06–12), afternoon (12–18), evening (18–24) — because a single all-day
average smooths out day/night variation. This was tested rigorously
(`ml-model/test_timewindow_full.py`, full 200-machine fleet, threshold-tuned
comparison):

| Baseline strategy | F1 (tuned threshold) | ROC-AUC |
|---|---|---|
| Per-machine, all-day | 0.6434 | 0.9066 |
| **Per-machine, per-time-window (shipped)** | **0.6475** | **0.9091** |
| Per-machine + explicit time features (hour_sin/cos) | 0.6064 | 0.8964 |

Explicit time features were tested and **rejected**: the per-machine baseline
already implicitly encodes temporal patterns, so adding time as a separate
column is redundant and slightly hurts the decision boundary.

**Four-level fallback chain** for machines the model has never seen:
`machine+window → machine (all-day) → machine type → global fleet average`.
The API reports which level was used on every prediction (`baseline_used`).

### Why 6 features

The model started with 3 features (cpu, ram, network) and was expanded to 6:
`cpu, ram, network, disk_io, disk_usage, load_avg`. Three features miss
entire classes of real incidents — a disk filling up produces almost no
signal in cpu/ram/network. With 6 features, disk saturation anomalies are
detectable (disk_usage z=4.93, load_avg z=5.31 while cpu/ram/network stay
near zero, proved with a live `/predict` demo).

**Network gear** (router/firewall/dns/voip) has near-zero disk/load metrics
by design, not a shortcut: they are SNMP-monitored appliances with no
physical disk (they boot from flash), matching real telco edge architecture
where compute nodes connect through a transport router.

### Data resolution: 1-minute vs 30-second (tested, adopted)

The model currently trains on 30-second-resolution synthetic data (one
reading per machine every 30 seconds), switched from an original 1-minute
resolution after a controlled full-scale experiment: 200 machines, 30 days,
matched anomaly ratio (~6.75%), same seed and hyperparameters, only the
sampling interval changed.

| Metric | 1-minute (previous) | 30-second (current) | Delta |
|---|---|---|---|
| F1 | 0.648 | 0.663 | +0.015 |
| ROC-AUC | 0.924 | 0.926 | +0.002 |
| Recall | 0.650 | 0.667 | +0.017 |
| Dataset size | 613 MB | 1226 MB | 2× |
| Full retrain time | 2m 4s | 3m 55s | 2.1× |

Result: finer resolution genuinely improves every metric, modestly and
reproducibly. It also has a real cost: generating the 30-second dataset at
full scale was killed by the Linux OOM killer on first attempt (confirmed
via `dmesg`: `anon-rss:4133168kB` before termination on a 7.7GB VM),
succeeding only on retry with more available memory. We adopted 30-second
resolution because the accuracy gain was consistent and the memory
constraint was resolved — but the tradeoff (2× storage, 2× retrain time,
real memory risk on constrained hardware) is documented, not glossed over.

### Cascading failures and root cause analysis

**What was tried and reverted.** A two-layer dependency model was built and
tested: a network layer (router → downstream machines, shipped) and a
service layer (web/edge → app → db/cache/queue, application-level call
dependencies mirroring a standard N-tier architecture). The service layer
was tested at full scale and reverted:

| Configuration | F1 (Isolation Forest) | F1 (z-threshold) |
|---|---|---|
| Network-only (shipped) | 0.663 | 0.657 |
| + service-tier correlation | 0.599 | 0.572 |

Every benchmarked model regressed under service-tier correlation, not just
Isolation Forest — confirming the issue was data quality, not a
model-specific weakness. Root cause: service-tier correlation inflated the
variance absorbed into each downstream machine's baseline (mean/std),
particularly for types sitting 1–2 dependency hops away (app, batch, web,
edge), widening what counts as "normal" and making genuine anomalies harder
to distinguish from correlated-but-expected stress. The change was reverted
to the network-only model, verified byte-identical (SHA-256 match) to the
last known-good commit — zero residual drift.

**What was kept and built on: root cause analysis using the network-layer
graph.** `build_dependency_graph.py` extracts the router-to-machine
relationship already implicit in the anomaly-correlation logic and persists
it as `models/dependency_graph.json` (8 routers, 180 machines with a tracked
dependency; 12 machines — firewall and dns — intentionally excluded, since
they are not assigned a router dependency in the topology).

`ml-model/root_cause.py`, exposed via `POST /root-cause`, ranks a batch of
currently-anomalous machines by root-cause likelihood, following the
approach used by dependency-graph-based RCA systems in production (e.g.
MicroHECL at Alibaba):

```
root_cause_score = own_anomaly_score + 0.15 × anomalous_downstream_count
```

Verified end to end: given 5 anomalous machines (1 router + 3 of its
dependents + 1 unrelated machine), the router was correctly ranked #1
despite having the LOWEST raw anomaly score (0.55 vs the unrelated
machine's 0.71) — because it explains 3 of the other 4 alerts. Verified
live in the monitoring pipeline too: across a full session, all 8 routers
in the fleet independently triggered real incidents and were correctly
identified as `likely_root_cause` every time, with their dependents
correctly tagged `downstream_effect`.

**Known limitation:** this only models the network-layer (router)
dependency. Real core telecom networks use mesh topologies with redundant
paths, not the simplified star topology modeled here. Application-level
dependencies (web calling a database) were tested and found to degrade
detection accuracy, so they are intentionally not part of either the
training data or the root-cause graph.

---

## ML model v2: labeled cause classification (July 2026)

The original Isolation Forest (above) is unsupervised and flags anomalies
without saying why. A second experiment track added labeled cause data and
a supervised classifier on top, evaluated with a stricter methodology than
the original benchmark: training and test data are two **independently
generated** 30-day, 200-machine datasets (different random seeds), so the
test set was never seen in any form during training — not even a different
time-slice of the same file.

**Generator upgrade:** `generate_telecom_fleet.py` now emits a labeled
`anomaly_type` column (`cpu_spike`, `memory_leak`, `network_flood`,
`disk_saturation`, `silent_failure`, `cascade`) alongside the existing
binary `label`, without changing any existing behavior.

**Two models, evaluated on the independent test set:**

| Model | F1 | Precision | Recall |
|---|---|---|---|
| **RandomForest (supervised, primary)** | **0.731** | 0.849 | 0.641 |
| Isolation Forest (unsupervised, safety net) | 0.652 | 0.655 | 0.649 |

Per-cause recall, RandomForest: cpu_spike 0.858, memory_leak 0.688,
network_flood 0.881, disk_saturation 0.965, silent_failure 0.868,
cascade 0.029.

**Why both models are kept, not just the better one:** `cascade` labels
are only 40% consistent by generator design (a downstream machine affected
by a router failure is labeled anomalous 40% of the time, unlabeled 60%,
for the identical feature pattern) — not cleanly learnable by a supervised
classifier. RandomForest trained on this label alone collapsed to F1=0.513
(cascade precision 0.16, poisoning the other classes). Folding cascade
into "normal" during training fixed that (F1=0.731) but left RandomForest
blind to cascades specifically (0.029 recall). Isolation Forest, being
unsupervised, has no such blind spot (0.262 cascade recall) since it
reacts to any statistical deviation regardless of label noise. Production
therefore runs both: RandomForest as the primary detector with cause
explanation, Isolation Forest as a safety net for patterns — like
cascades — the classifier structurally cannot learn.

**Two architectural fixes attempted and rejected, with evidence:**

| Attempt | F1 | Why it failed |
|---|---|---|
| Blind ensemble (flag if either model fires) | 0.663 | Inherited Isolation Forest's false positives on top of RandomForest's correct calls |
| Dependency-graph cascade rule (flag all downstream machines when router predicted anomalous) | 0.550 | Each router has ~22 downstream machines; one wrong router prediction produced ~22 wrong flags (precision on cascade-rule-fired rows: 2.9%) |

Both are documented here rather than discarded silently — they are real,
informative negative results, not implementation bugs.

**A fourth alternative was also explored:** a separate generator variant
adding two additional features (`response_time`, `packet_loss`) and
gradual-onset anomaly injection (severity ramping linearly over the
anomaly's duration, rather than applying full severity instantly, as the
production generator does). Evaluated with a threshold search on the test
set itself -- an optimistic evaluation that should favor this variant --
it still underperformed the production pipeline (F1=0.522 vs 0.644-0.731).
Per-cause results were highly uneven (silent_failure 0.918, network_flood
0.839, but memory_leak only 0.071): gradual onset appears to make
snapshot-based z-score detection harder for slow-building anomalies,
since metrics are only mildly elevated for most of the anomaly's
duration. This suggests gradual onset is a more realistic injection model
but would need trend-aware features (rate of change, rolling statistics)
rather than single-timestamp z-scores to detect well -- consistent with
the sequence-aware future-work direction noted in the Roadmap.

**Real-world validation on SMD (Server Machine Dataset):** the same
per-machine, per-time-window z-score methodology was applied unmodified to
SMD — 28 real servers, 708K rows, 4.16% anomaly rate, the public benchmark
used in Su et al. (KDD 2019, OmniAnomaly). Three variants tested:

| Variant | Features | F1 |
|---|---|---|
| Per-machine, no window | 37 (all) | 0.248 |
| **Per-machine + time window** | 37 (all) | **0.269** |
| Top-3 variance features only | 3 | 0.159 |

F1=0.269 is lower than the synthetic-data results, expected and consistent
with the literature: published unsupervised sequence models on SMD
(OmniAnomaly and similar GRU-VAE architectures) report F1 in the 0.40–0.55
range, but those models read a *sliding window* of recent history rather
than one timestamp in isolation — a fundamentally more powerful signal for
gradual-onset anomalies, at the cost of a much heavier architecture
(recurrent neural networks vs. IsolationForest/RandomForest here). This
project prioritizes training speed, interpretability, and straightforward
CI/CD-integrated deployment over the marginal accuracy gains of a
recurrent sequence model — a deliberate, documented tradeoff, not an
oversight (see Roadmap for the future-work framing). The SMD result's
purpose is not to win on the leaderboard; it confirms the same
methodology generalizes to real, independently-collected data and isn't
an artifact of the synthetic generator.

**Model artifacts:** `models/telecom_rf_classifier_v2.pkl` (RandomForest,
not committed — 125MB exceeds GitHub's 100MB limit, fully reproducible via
`generate_telecom_fleet.py --seed 42` + the training pipeline,
`random_state=42`, deterministic), `models/telecom_iso_v2.pkl`
(IsolationForest, committed), `models/telecom_baselines_v2.json`.

## API

FastAPI service, model baked into the Docker image. Rebuild cost is
seconds, so image tag = exact model version.

```
GET  /health       service status, feature list, fallback chain
GET  /machines     all known machines with type
POST /predict      anomaly score for one machine reading
POST /root-cause   rank a batch of anomalous machines by root-cause likelihood
```

### POST /predict

```json
{
  "machine": "web-01",
  "hour": 14,
  "metrics": {
    "cpu": 30, "ram": 50, "network": 80,
    "disk_io": 25, "disk_usage": 40, "load_avg": 1.2
  }
}
```

`hour` (0–23) or `timestamp` (ISO 8601) is optional — omitting it falls back
to the machine's all-day baseline. Response includes `is_anomaly`,
`anomaly_score`, per-feature `z_scores` (operator-facing explainability —
"cpu z=7.4" tells the ops team exactly which metric is driving the alert),
and `baseline_used` (which fallback level fired).

**With `MODEL_NAME=telecom_v2`**, `/predict` additionally returns
`likely_cause` (one of the 5 named anomaly types, or `null` if normal, or
"unknown" if only the Isolation Forest safety net fired) plus `rf_vote`
and `iso_vote` showing each model's independent decision — see the
"ML model v2" section above. Live-tested against three distinct anomaly
signatures (disk_saturation, memory_leak, network_flood): all three
correctly identified with matching z-score evidence.

### POST /root-cause

```json
{
  "anomalies": {
    "router-01": 0.55,
    "web-01": 0.62,
    "web-09": 0.58,
    "app-01": 0.60,
    "mystery-01": 0.71
  }
}
```

Takes machine names and their anomaly scores (from prior `/predict` calls),
returns them ranked by root-cause likelihood with a role assigned to each:
`likely_root_cause`, `downstream_effect`, or `isolated`. Also returns a
clean `likely_root_causes` list for direct use in an alert summary.

---

## Docker (L2)

- Base: `python:3.10-slim`, non-root `appuser`, layer-cached deps, model and
  dependency graph baked into the image
- `HEALTHCHECK` on `/health`, same endpoint K8s liveness probes would use
- Local registry (`registry:2`, port 5000) for Jenkins to push to

```bash
docker build -t devsecmlops-api:2.4.0 .
docker run -p 8000:8000 devsecmlops-api:2.4.0
```

---

## CI/CD (L3)

7-stage Jenkins pipeline, triggered from `git push` (private repo, PAT
credential):

1. Checkout
2. SAST — real `sonar-scanner` against `api/` and `ml-model/`, webhook-driven
   quality gate (`waitForQualityGate`, SonarQube pushes the result to
   Jenkins rather than Jenkins polling)
3. Docker build (tagged with build number)
4. Container smoke test
5. Trivy CVE scan (HIGH/CRITICAL)
6. Push to local registry
7. Deploy placeholder (pull-back verification; real K8s deploy is L4)

Security fixes verified through this pipeline, not just manually: 2 HIGH
CVEs (CVE-2026-24049 wheel, CVE-2026-23949 jaraco.context, both transitive
setuptools dependencies) found by Trivy, fixed by pinning
`setuptools>=70.0.0 wheel>=0.46.2`, confirmed clean (0 findings) both
manually and via an automated pipeline run. 4 SonarQube reliability issues
(`DataFrame.values`, `np.where` usage, dict comprehension patterns) found
and fixed, Reliability rating D → A.

Requires: SonarQube Scanner plugin, `sonarqube-token` credential (Secret
text), `sonar-scanner` CLI installed in the Jenkins container, a dedicated
Docker network (`devsecmlops-net`) for stable container-name DNS resolution
between the jenkins, sonarqube, and registry containers.

---

## Kubernetes (L4)

Minikube, single-node, Docker driver. Single namespace: `ml-serving`
(API workloads). MLflow and MinIO run as standalone Docker containers
outside the cluster, not in a K8s namespace -- see MLOps section below
for why.

- **Deployment:** 2 replicas of `devsecmlops-api:2.4.0`
- **Service:** NodePort 30080
- **HorizontalPodAutoscaler:** 2–5 replicas, target 70% CPU
- **Security:** non-root `securityContext` (`runAsUser: 1000`,
  `allowPrivilegeEscalation: false`), CPU/memory requests and limits set
- **Reliability:** liveness and readiness probes on `/health`
  (10s/5s initial delay) — Kubernetes automatically restarts a pod that
  stops responding correctly, and only routes traffic to pods that pass
  the readiness check

Image loading: `imagePullPolicy: Never`, with the image loaded directly
into Minikube's Docker daemon (`minikube image load`) rather than pulled
from the local registry. Minikube's Docker driver could not be reliably
configured with `--insecure-registry` in this environment (the internal
`host.minikube.internal` hostname did not resolve correctly with the
Docker driver on this Linux setup), so direct image loading was used as
the pragmatic alternative for a single-node demo cluster. A production
multi-node cluster would pull from a real registry (Harbor, ECR, etc.)
instead — the Deployment manifest would only need the `image:` field and
`imagePullPolicy` changed, nothing else.

**Metrics-server enabled** for real HPA readings (`kubectl top pods/nodes`
now returns actual CPU/memory usage instead of `<unknown>`).

**Operational incident, documented:** restarting the Docker daemon while
Minikube is running leaves the cluster's internal networking in a stale,
unreachable state even though the container itself stays "Up" — diagnosed
via a drifting host-port mapping that invalidated `kubectl`'s cached
config. Resolved with a full `minikube delete` + fresh start. Standing
rule adopted: always run `minikube start` (not `docker start minikube`)
after any Docker daemon restart, since only `minikube start` properly
re-establishes cluster networking and certificates.

Verified end-to-end through the K8s-managed service (not just a bare
Docker container):

```
$ curl http://$(minikube ip):30080/health
status=ok version=2.4.0 n_features=6 machines=200 root_cause=True

$ curl -X POST http://$(minikube ip):30080/predict ... (disk saturation)
machine=db-01 is_anomaly=1 score=0.6232 disk_usage_z=4.93 load_avg_z=5.31

$ curl -X POST http://$(minikube ip):30080/root-cause ...
likely_root_causes=['router-01']
  router-01    score=0.55  role=likely_root_cause
  web-01       score=0.62  role=downstream_effect
  app-01       score=0.60  role=downstream_effect
  web-09       score=0.58  role=downstream_effect
  mystery-01   score=0.71  role=isolated
```

```bash
minikube start --driver=docker --force
minikube addons enable metrics-server
minikube image load devsecmlops-api:2.4.0
kubectl apply -f kubernetes/namespace.yaml
kubectl apply -f kubernetes/deployment.yaml
kubectl apply -f kubernetes/service.yaml
kubectl apply -f kubernetes/hpa.yaml
kubectl get pods -n ml-serving
kubectl top pods -n ml-serving
```

---

## Monitoring (L5)

Full-stack observability: Prometheus + Grafana (20 panels) + a real-data
replay engine + Kubernetes monitoring, all built on top of the trained
model and the root-cause endpoint.

### Real-data replay, not random simulation

`monitoring/replay_exporter.py` replays the ACTUAL evaluation dataset
(`data/telecom_fleet.csv`, the exact 200-machine, 30-day, 30-second-
resolution data the model was trained and validated against — F1=0.663,
ROC-AUC=0.926) through Prometheus, cycling continuously. This was a
deliberate upgrade from an earlier random-noise simulator
(`fleet_simulator.py`, superseded): the live demo now uses data provably
identical to what the model was evaluated on, not freshly generated
numbers that only approximate it.

It also exposes the dataset's real ground-truth label
(`sim_ground_truth_anomaly`) alongside the model's live prediction —
enabling a direct "did the model agree with reality" comparison panel,
not just "did the model flag something."

### A critical bug found and fixed

The anomaly bridge originally used real wall-clock time
(`datetime.now().hour`) to select which per-time-window baseline to
compare against. But the replay exporter runs on an independent simulated
30-day timeline that has nothing to do with real time. This mismatch
(e.g. comparing a replayed night-quiet reading against an afternoon-peak
baseline) caused up to 181 of 200 machines to falsely appear anomalous
simultaneously. Fixed by publishing the replay's actual simulated hour
(`sim_replay_hour`) and having the bridge read that instead — anomaly
counts returned to realistic levels (single digits to ~15) immediately
after the fix. Documented here because it is a genuine, instructive
example of a synchronization bug between two independently-clocked
components.

### anomaly_bridge.py

Polls Prometheus every 30s for all 200 machines, calls `/predict` on
each, batches multi-machine anomalies into `/root-cause`, and publishes
its own findings back to Prometheus (`bridge_is_anomaly`,
`bridge_anomaly_score`) so Grafana can visualize live detection state,
not just raw fleet metrics.

Verified live: a router set anomalous correctly stressed its real
`dependency_graph.json` dependents; `/root-cause` correctly identified
the router as `likely_root_cause` across every cycle of the incident,
correctly tagged dependents as `downstream_effect`, and correctly
separated unrelated background noise as `isolated`. Across the full
session, all 8 routers in the fleet independently triggered and were
correctly detected at least once.

### Kubernetes monitoring

`monitoring/k8s_exporter.py` publishes pod readiness, restart counts,
deployment replica counts, and HPA CPU utilization as Prometheus metrics,
using `kubectl` as the data source rather than scraping the K8s API
server directly — a deliberate choice after Minikube's internal
networking proved fragile (see Kubernetes section above).

### Grafana dashboard (20 panels)

Live anomaly detection, root-cause event history (proved all 8 routers
triggered real incidents this session), fleet-wide averages for all 6
features, model-prediction-vs-ground-truth comparison, real host-VM
health (Node Exporter), and Kubernetes pod/replica/HPA status — one
dashboard covering the model, the API, the infrastructure, and the
orchestration layer.

A `$machine_type` template variable filters every panel by machine type
(router, web, db, etc., or All).

### Machine roster

`docs/machine_roster.txt`: complete identification of all 200 simulated
machines — name, type, and a plain-English description of its role
(e.g. `db-01 / db / Database - persistent data storage`).

---

## MLOps: experiment tracking and model registry

MLflow (tracking + model registry) backed by MinIO (self-hosted
S3-compatible artifact storage). This closes a real gap from the
original architecture proposal: MLflow/MinIO were planned but not
built until this point in the project.

**MinIO** (`docker run minio/minio`, ports 9001 API / 9002 console):
generic object storage, playing the role AWS S3 would in a cloud
deployment, but self-hosted -- satisfying the data-sovereignty
requirement (no cloud SaaS in the critical path) from the original
Tunisie Telecom constraints.

**MLflow** (`mlflow server`, port 5001, SQLite backend + MinIO artifact
store): every run of `train_serving_telecom.py` now logs:
- **Parameters:** contamination, n_estimators, n_features, n_machines,
  resolution_seconds
- **Metrics:** F1, Precision, Recall, ROC-AUC
- **Model artifact:** registered to the Model Registry as
  `telecom-anomaly-model`, versioned

Verified with a real training run: F1=0.663, Precision=0.660,
Recall=0.667, ROC-AUC=0.926 -- exactly matching prior benchmark
numbers, confirming the MLflow integration adds tracking without
changing model behavior.

**Honest scope note:** the Docker build/deploy pipeline still uses the
git-committed model artifact (`telecom_serving_model.pkl`) directly --
it has not been rewired to pull from the MLflow registry. This was a
deliberate choice to avoid introducing a new runtime dependency into
the working CI/CD path this late in the project. A natural next step
would be having the Jenkins Docker-build stage fetch the latest
Production-stage model version from MLflow instead of reading the
git-committed file.

**Why MLflow/MinIO run as standalone Docker containers, not inside the
Kubernetes cluster:** same reasoning as Jenkins, SonarQube, and the
registry -- these are shared platform/tooling services supporting the
development process, not the product being served to end users. In a
real organization, one MLflow server typically tracks experiments
across many projects; it would not live inside a single project's own
K8s namespace.

```bash
# MinIO
docker run -d --name minio --restart=always \
  -p 9001:9000 -p 9002:9001 \
  -e "MINIO_ROOT_USER=admin" -e "MINIO_ROOT_PASSWORD=minioadmin123" \
  -v minio_data:/data \
  minio/minio server /data --console-address ":9001"

# MLflow (create the mlflow-artifacts bucket in MinIO's console first)
export MLFLOW_S3_ENDPOINT_URL=http://localhost:9001
export AWS_ACCESS_KEY_ID=admin
export AWS_SECRET_ACCESS_KEY=minioadmin123
mlflow server --host 0.0.0.0 --port 5001 \
  --backend-store-uri sqlite:///mlflow.db \
  --default-artifact-root s3://mlflow-artifacts/
```

---

## Repository layout

```
api/
  app.py                       FastAPI service, includes /root-cause

ml-model/
  preprocess.py                baseline construction + z-score + 4-level fallback
  train_serving_telecom.py     final trainer (production artifact)
  generate_telecom_fleet.py    synthetic 200-machine fleet generator (6 features)
  build_dependency_graph.py    extracts the router→machine dependency graph
  root_cause.py                root cause scoring logic
  benchmark.py                 6-model comparison harness
  test_timewindow_full.py      baseline-strategy experiment (evidence)
  zscore_demo.py               worked example / defense demo (3 parts)

models/
  telecom_serving_model.pkl        shipped Isolation Forest
  telecom_serving_baselines.json   800 window + 200 machine + 11 type + global
  dependency_graph.json            router → machine relationships
  results/                         benchmark JSON evidence files

data/                    generated fleet CSV (not committed — regenerate locally)

Dockerfile                API image, model + dependency graph baked in
.dockerignore             excludes venv/, data/, old models
requirements-api.txt      serving-only deps, pinned versions
Jenkinsfile               7-stage CI/CD pipeline

kubernetes/               K8s manifests (namespace, deployment, service, HPA)
monitoring/
  prometheus.yml               scrape config: node-exporter, replay,
                                anomaly-bridge, k8s-exporter
  replay_exporter.py           replays the real evaluation dataset live
  anomaly_bridge.py            polls Prometheus, calls /predict + /root-cause
  k8s_exporter.py              publishes pod/replica/HPA status via kubectl
  grafana_dashboard.json       20-panel dashboard definition

docs/
  machine_roster.txt           all 200 machines: name, type, role

scripts/
  full_verification.sh         one-shot health check across all 6 layers
  capture_all_screenshots.sh   defense screenshot automation

screenshots/              defense evidence PNGs
```

---

## Reproducing locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Generate the synthetic fleet (200 machines, 30 days, 6 features, 30s resolution)
python ml-model/generate_telecom_fleet.py --machines 200 --days 30 --anomaly-ratio 0.05

# Train the serving model
python ml-model/train_serving_telecom.py

# Extract the dependency graph
python ml-model/build_dependency_graph.py

# Benchmark against 5 alternative methods
python ml-model/benchmark.py --data data/telecom_fleet.csv --contamination 0.068 --n-estimators 200 --max-rows 80000

# Run the z-score demo (defense script)
python ml-model/zscore_demo.py

# Serve directly
MODEL_NAME=telecom uvicorn api.app:app --host 0.0.0.0 --port 8000

# Or containerized
docker build -t devsecmlops-api:2.4.0 .
docker run -p 8000:8000 devsecmlops-api:2.4.0

# Test endpoints
curl localhost:8000/health
curl -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d '{"machine":"web-01","hour":14,"metrics":{"cpu":30,"ram":50,"network":80,"disk_io":25,"disk_usage":40,"load_avg":1.2}}'
curl -X POST localhost:8000/root-cause -H "Content-Type: application/json" \
  -d '{"anomalies":{"router-01":0.55,"web-01":0.62}}'

# Full-stack monitoring
python monitoring/replay_exporter.py &   # replays real evaluation data
python monitoring/anomaly_bridge.py &    # detects + explains, every 30s
python monitoring/k8s_exporter.py &      # publishes K8s pod/HPA status
# Prometheus + Grafana: import monitoring/grafana_dashboard.json
```

---

## Roadmap

- **L6 — Ansible:** single playbook to reproduce the entire platform from
  a fresh bare Ubuntu VM
- **Continuous learning (documented, not implemented):** a tiered
  retraining design was scoped but not built. New-machine detection and
  lightweight baseline updates (online mean/std, e.g. Welford's algorithm)
  could run every 30–60 seconds cheaply; full model retraining should run
  on a much slower cadence (daily/weekly) since a full retrain was measured
  at 3m 55s on the current dataset — far too slow to trigger every 30
  seconds without overlapping runs and resource contention. Baselines and
  the model must always be regenerated together, never independently,
  since the model's learned decision boundary is calibrated to the
  specific z-score distribution its baseline produces.
- **Richer dependency graph:** the current graph models only the network
  layer (router → machine, star topology, no redundancy). A mesh topology
  matching real core-network resilience, and a validated service-call
  layer (the service-tier experiment above showed the naive approach
  degrades detection — a better approach would need to decouple the
  correlation injection from the baseline computation, e.g. training on
  uncorrelated data and only using the service graph for root-cause
  ranking, never for anomaly injection) are both natural next steps.
- **Alertmanager integration:** wire real Slack/email notifications when
  `/root-cause` identifies a `likely_root_cause`, closing the loop from
  detection to human notification.
- **Sequence-aware detection:** the current models (Isolation Forest,
  RandomForest) evaluate each timestamp independently, which limits
  sensitivity to gradual-onset anomalies such as memory leaks (currently
  the weakest per-cause recall, 0.688). State-of-the-art approaches on
  SMD -- notably OmniAnomaly (Su et al., KDD 2019), a GRU-VAE architecture
  that models temporal dependence across a sliding window of readings --
  report higher F1 by using this recent-history signal. A simpler,
  lower-risk step toward the same idea (rolling/delta features -- e.g.
  `cpu_delta`, rolling mean/std over the last N readings) was scoped but
  not integrated into the shipped pipeline; a full recurrent sequence
  model is the natural following iteration.
