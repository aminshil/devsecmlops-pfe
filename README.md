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
| L4 | Kubernetes (Minikube) | 📋 Planned |
| L5 | Monitoring (Prometheus, Grafana, Node Exporter, anomaly bridge) | 📋 Planned |
| L6 | Ansible (infrastructure as code) | 📋 Planned |

**Current model:** F1 = 0.663 · Precision = 0.660 · Recall = 0.667 · ROC-AUC = 0.926
on a 200-machine synthetic Tunisie Telecom fleet, 6.75% anomaly rate, 6 features,
30-second sampling resolution.

**Full version history:** see [GitHub Releases](https://github.com/aminshil/devsecmlops-pfe/releases)
— 17 tagged releases from v0.1.0 (initial POC) through v2.3.0, each with
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
                                   Kubernetes: FastAPI pods (2+ replicas) [planned]
                                                          │
                        ┌─────────────────────────────────┼─────────────────────┐
                        ▼                                 ▼                     ▼
              Node Exporter (fleet)              Prometheus scrape      Grafana dashboards
                                                          │
                                                          ▼
                              Anomaly bridge → /predict → /root-cause → Alertmanager
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
machine's 0.71) — because it explains 3 of the other 4 alerts. The
unrelated machine was correctly identified as `isolated`; the router's
dependents were correctly tagged `downstream_effect` with their
`upstream_router` identified.

**Known limitation:** this only models the network-layer (router)
dependency. Real core telecom networks use mesh topologies with redundant
paths, not the simplified star topology modeled here. Application-level
dependencies (web calling a database) were tested and found to degrade
detection accuracy, so they are intentionally not part of either the
training data or the root-cause graph.

---

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
docker build -t devsecmlops-api:2.7.0 .
docker run -p 8000:8000 devsecmlops-api:2.7.0
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

kubernetes/               K8s manifests (L4, planned)
monitoring/               Prometheus/Grafana config (L5, planned)
scripts/                  utility scripts (screenshot capture)
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
docker build -t devsecmlops-api:2.7.0 .
docker run -p 8000:8000 devsecmlops-api:2.7.0

# Test endpoints
curl localhost:8000/health
curl -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d '{"machine":"web-01","hour":14,"metrics":{"cpu":30,"ram":50,"network":80,"disk_io":25,"disk_usage":40,"load_avg":1.2}}'
curl -X POST localhost:8000/root-cause -H "Content-Type: application/json" \
  -d '{"anomalies":{"router-01":0.55,"web-01":0.62}}'
```

---

## Roadmap

- **L4 — Kubernetes:** Minikube on the VM, `ml-serving` + `infra`
  namespaces, Deployment with 2+ replicas, HPA on CPU, non-root
  securityContext, Service (NodePort :30080), liveness/readiness probes on
  `/health`
- **L5 — Monitoring:** Node Exporter on the fleet → Prometheus :9090 →
  anomaly bridge (CronJob polls Prometheus, calls `/predict` for each
  machine, batches anomalies into `/root-cause`) → Grafana :3000
  dashboards + Alertmanager → Slack/email
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
