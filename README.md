# DevSecMLOps Platform

**Conception et mise en œuvre d'une plateforme DevSecMLOps Cloud-Native**
PFE ESPRIT × Tunisie Telecom — 2025–2026 — Amine Shil

A self-hosted, cloud-native platform that detects anomalies on IT infrastructure
(CPU, RAM, network, disk, load) using machine learning, delivered through a
fully automated, security-gated CI/CD pipeline.

---

## Status

| Layer | Component | Status |
|-------|-----------|--------|
| L0 | ML model — Isolation Forest, per-machine per-time-window z-score | ✅ Done |
| L1 | FastAPI serving layer | ✅ Done |
| L2 | Docker + local registry | ✅ Done |
| L3 | Jenkins CI/CD (7 stages, SonarQube, Trivy) | 🔨 In progress |
| L4 | Kubernetes (Minikube) | 📋 Planned |
| L5 | Monitoring (Prometheus, Grafana, Node Exporter) | 📋 Planned |
| L6 | Ansible (infrastructure as code) | 📋 Planned |

**Current model (v2.3.0):** F1 = 0.648 · Precision = 0.646 · Recall = 0.650 · ROC-AUC = 0.924
on a 200-machine synthetic Tunisie Telecom fleet, 6.77% anomaly rate, 6 features.

---

## Architecture

```
Git push → Jenkins (SonarQube → train → F1 gate → Docker build → Trivy scan → deploy)
                                                          │
                                                          ▼
                                          Docker image (model baked in)
                                                          │
                                                          ▼
                                   Kubernetes: FastAPI pods (2+ replicas)
                                                          │
                        ┌─────────────────────────────────┼─────────────────────┐
                        ▼                                 ▼                     ▼
              Node Exporter (fleet)              Prometheus scrape      Grafana dashboards
                                                          │
                                                          ▼
                                         Anomaly bridge → /predict → Alertmanager
```

Everything runs on a single self-hosted VM for the PFE demo. The same Docker
images and Kubernetes manifests deploy unmodified to a multi-node production
cluster — only configuration changes, not code.

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
flagged point inside an anomaly block counts the whole block as detected --
the metric commonly reported in time-series anomaly detection research.

Honest reading of this table: the simple z-threshold rule is competitive on
strict F1, and even modestly ahead of Isolation Forest (0.657 vs 0.626). We
still ship Isolation Forest for three reasons a single F1 number does not
capture:

1. **Better ranking quality.** ROC-AUC (0.922 vs 0.914) and PR-AUC@adj
   (0.722 vs 0.710) both favor Isolation Forest -- it ranks anomalies more
   reliably across all possible thresholds, not just the one z-threshold
   happens to use.
2. **Continuous, tunable score.** z-threshold is a fixed per-feature cutoff
   (exactly 3 sigma). Isolation Forest exposes a continuous `anomaly_score`
   (already in the API response) that operators can re-tune via
   `contamination` without code changes.
3. **Multivariate detection.** z-threshold flags a reading if ANY single
   feature exceeds 3 sigma. It cannot catch a case where four features are
   each moderately elevated (say 2 sigma each) but jointly represent a real
   incident -- a structural gap the per-feature rule cannot close, that
   Isolation Forest's split-based structure can.

Isolation Forest was chosen deliberately, not by default: it also requires
no labeled anomalies (production has none), infers in under 10ms, needs only
one key hyperparameter (`contamination`), and scales cleanly to 200+
machines. Comparing "Isolation Forest, raw" vs "z-scored" in the table above
shows the z-score normalization step is what actually drives its
performance -- raw Isolation Forest scores 0.361 F1; z-scored reaches 0.626.

### Why unsupervised, not supervised

A supervised classifier (Random Forest, XGBoost, etc.) would score higher F1
on this labeled synthetic dataset, but it needs labeled anomalies to train.
Production infrastructure has none — nobody manually labels every anomalous
minute across 200 servers. Isolation Forest learns "normal" from the incoming
metric stream with zero labels, and can flag anomaly *types* it has never
seen before. The dataset's `label` column is used only to evaluate the model,
never to train it.

### Why per-machine, per-time-window baselines (not global, not raw)

Every metric is z-scored *before* reaching the model:

```
z = (raw_value - machine's own mean) / machine's own std
```

A web server idling at 30% CPU and a database server running hot at 75% CPU
are both "normal" — a single global threshold can't capture that, but a
per-machine baseline can.

Baselines are split further into four **time windows** — night (00–06),
morning (06–12), afternoon (12–18), evening (18–24) — because a single
all-day average smooths out day/night variation. A machine idling at 30% CPU
at night that jumps to 50% CPU still at night is anomalous, but a same-machine
50% CPU reading during the day is completely normal. Splitting the baseline
by time window lets the model catch subtle time-dependent anomalies that a
flat, all-day baseline misses.

This was tested rigorously, not assumed (`ml-model/test_timewindow_full.py`,
full 200-machine fleet, threshold-tuned comparison):

| Baseline strategy | F1 (tuned threshold) | ROC-AUC |
|---|---|---|
| Per-machine, all-day | 0.6434 | 0.9066 |
| **Per-machine, per-time-window (shipped)** | **0.6475** | **0.9091** |
| Per-machine + explicit time features (hour_sin/cos) | 0.6064 | 0.8964 |

Explicit time features (adding `hour_sin`, `hour_cos` as extra columns) were
tested and **rejected**: the per-machine baseline already implicitly encodes
temporal patterns (its mean is computed over the full day/night cycle), so
adding time as a separate feature is redundant and slightly hurts the
Isolation Forest's decision boundary in low-dimensional space. Splitting the
*baseline itself* by time window, instead of adding time as a model feature,
avoids that redundancy while still capturing the temporal signal.

**Four-level fallback chain** for machines the model has never seen:
`machine+window → machine (all-day) → machine type → global fleet average`.
The API reports which level was used on every prediction (`baseline_used`),
making the fallback auditable.

### Why 6 features, and why network gear doesn't get all of them

The model started with 3 features (cpu, ram, network) and was expanded to 6:
`cpu, ram, network, disk_io, disk_usage, load_avg`.

**Why expand:** 3 features miss entire classes of real incidents. A disk
filling up, or a disk I/O storm, produces almost no signal in cpu/ram/network
— the model is structurally blind to it. Benchmarked on the full fleet, 6
features raised the shipped model from F1=0.641 to F1=0.648 with ROC-AUC
0.918 → 0.924, and — more importantly — made an entire failure category
(disk saturation) detectable at all.

**Why not more than 6:** Isolation Forest degrades in high-dimensional spaces
(the "curse of dimensionality" — splits become less informative as dimensions
grow). Six complementary, low-correlation metrics is close to the practical
ceiling for this model family at this data volume; the six chosen map
directly to what real monitoring agents actually export.

**Why network gear (router, firewall, dns, voip) has near-zero disk/load:**
This is not a shortcut, it reflects real hardware. Servers
(web/app/db/cache/queue/batch/edge) run Linux and expose all 6 metrics via
Node Exporter. Network appliances like Cisco routers and firewalls are
monitored via **SNMP**, not Node Exporter, and physically have no spinning
disk — they boot from flash. `disk_io`, `disk_usage`, and `load_avg` (a
Unix scheduler concept) don't meaningfully exist on that hardware. The
synthetic data generator (`ml-model/generate_telecom_fleet.py`) reflects
this: network-gear profiles carry near-zero disk/load noise floors instead
of fabricated values, and the `disk_saturation` anomaly type is disabled for
those machine types since it is physically impossible on diskless hardware.
The per-type baseline correctly learns that near-zero disk activity is
*normal* for a router, so it is never falsely flagged.

### Known limitation

A single mean/std per machine-window still smooths within that 6-hour
window. A very short, sharp spike inside one window could be partially
absorbed. Finer-grained (hourly) baselines were not pursued — the 4-window
split already captures the dominant day/night pattern, and finer windows
would fragment the training data per bucket on a 30-day dataset.

---

## API

FastAPI service, model baked into the Docker image (2 MB `.pkl` — rebuild
cost is seconds, so image tag = exact model version; no external model
registry needed at this scale).

```
GET  /health     service status, feature list, fallback chain
GET  /machines    all known machines with type
POST /predict     anomaly score for one reading
```

`POST /predict` body:
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
to the machine's all-day baseline.

Response includes `is_anomaly`, `anomaly_score`, per-feature `z_scores`
(for operator-facing explainability — "cpu z=7.4" tells the ops team exactly
which metric is driving the alert), and `baseline_used` (which fallback
level fired).

---

## Repository layout

```
api/                  FastAPI service (app.py)
ml-model/             training, benchmarking, data generation
  preprocess.py         baseline construction + z-score + fallback chain
  train_serving_telecom.py   final trainer (production artifact)
  generate_telecom_fleet.py  synthetic 200-machine fleet generator
  benchmark.py           6-model comparison harness
  test_timewindow_full.py    baseline-strategy experiment (evidence)
  zscore_demo.py         worked example / defense demo script
models/                trained artifacts + benchmark result JSONs (evidence)
data/                  generated fleet CSV (not committed — regenerate locally)
Dockerfile             API image, model baked in, non-root user
docker-compose.yml     local dev stack
kubernetes/            manifests (L4, in progress)
monitoring/            Prometheus/Grafana config (L5, in progress)
scripts/               utility scripts (screenshot capture, etc.)
screenshots/           defense evidence — benchmark tables, API demos
```

---

## Reproducing locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Generate the synthetic fleet (200 machines, 30 days, 6 features)
python ml-model/generate_telecom_fleet.py --machines 200 --days 30

# Train the serving model
python ml-model/train_serving_telecom.py

# Benchmark against 5 alternative methods
python ml-model/benchmark.py --data data/telecom_fleet.csv --max-rows 80000

# Serve
MODEL_NAME=telecom uvicorn api.app:app --host 0.0.0.0 --port 8000

# Or containerized
docker build -t devsecmlops-api:2.3.0 .
docker run -p 8000:8000 devsecmlops-api:2.3.0
```

---

## Roadmap

- **L3 — Jenkins:** 7-stage pipeline (checkout → SonarQube SAST → train + F1
  quality gate → Docker build → Trivy CVE scan → push → deploy)
- **L4 — Kubernetes:** Minikube, `ml-serving` + `infra` namespaces, HPA,
  non-root securityContext
- **L5 — Monitoring:** Node Exporter on the fleet → Prometheus → anomaly
  bridge (polls Prometheus, calls `/predict`) → Grafana + Alertmanager
- **L6 — Ansible:** playbook to reproduce the entire platform from a bare VM
