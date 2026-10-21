# DevSecMLOps Platform

**Conception et mise en œuvre d'une plateforme DevSecMLOps Cloud-Native**
PFE ESPRIT × Tunisie Telecom — 2025–2026 — Amine Shil

A self-hosted, cloud-native platform that detects anomalies on IT infrastructure (CPU, RAM, network, disk, load) using machine learning, delivered through a fully automated, security-gated CI/CD pipeline. Includes dependency-graph-based root cause analysis to distinguish a true root cause from downstream cascading victims when multiple machines alert at once.

> **About the data.** Every dataset in this repository is **simulated**, produced by `ml-model/generate_telecom_fleet.py` (200 machines, 11 equipment types, 30-second readings, labelled injected anomalies). It is not Tunisie Telecom data. All scores below are measured on that simulation, and the operator verdicts in the feedback loop are simulated too. The one check on real data (SMD) scores far lower, F1 0.269.

This document is organized **layer by layer (L0–L6)**. Every layer section follows the same shape: what it does, what was tested (including what was tried and rejected, with real numbers), real bugs found and fixed, and the files that make it up. Nothing here is asserted without a command, a log, or a test result behind it — where a number or a fix is described, the evidence is named alongside it.

---

## Contents

1. [At a glance](#at-a-glance)
2. [Executive summary](#executive-summary)
3. [Architecture](#architecture)
4. [Quick start](#quick-start)
5. [L0 — ML Model](#l0--ml-model)
6. [L1 — Serving API](#l1--serving-api)
7. [L2 — Container Image](#l2--container-image)
8. [L3 — CI/CD](#l3--cicd)
9. [L4 — Kubernetes](#l4--kubernetes)
10. [L5 — Observability and MLOps](#l5--observability-and-mlops)
11. [L6 — Ansible (Infrastructure as Code)](#l6--ansible-infrastructure-as-code)
12. [Security posture](#security-posture)
13. [Engineering decisions and rationale](#engineering-decisions-and-rationale)
14. [Testing](#testing)
15. [Reproducing locally](#reproducing-locally)
16. [Documentation and evidence](#documentation-and-evidence)
17. [Known limitations and future work](#known-limitations-and-future-work)

---

## At a glance

| Item | Current value | Evidence |
|---|---|---|
| Version | `2.20.3` | `VERSION`, reported by `/health` |
| Serving | `MODEL_NAME=telecom_v3`: hybrid path, **v4 rolling-features XGBoost** when a request carries a 10-reading history, v3 XGBoost otherwise, IsolationForest safety net on both | Live pod metrics: every observed request served by v4 (444 of 444 across both replicas at check time) |
| Decision threshold | `0.91`, **chosen by the pipeline** for each trained model on the separate seed-7 validation fleet: the highest precision whose recall is at least 0.80. It is read from the manifest at start-up; no deployment file carries a copy | `models/manifest.json` (`threshold_selection`), enforced by the CI model gate; `/health` → `model_lineage.threshold_source` |
| Where to change training and threshold behaviour | One file: `config/model_policy.json` (training size, recall target, threshold grid, guardrail limits) | [Current method](#current-method-one-policy-file-a-threshold-chosen-per-model) |
| Served v4 (independent test set, 17.28M rows) | F1 **0.7292** · precision 0.6713 · recall 0.7979 | `models/manifest.json`, re-checked by CI on every build. Several L0 analysis tables below were measured on earlier models (July: v4 F1 0.7246; 13 October: 0.7278; 19 October: 0.7291) |
| Served v3 fallback | F1 0.6667 · precision 0.5971 · recall 0.7545 | `models/manifest.json` |
| Weakest cause | `cascade`, recall 0.268 (label noise by design; a realistic model of router failures, built and tested at full size, is in [9c](#9c-failure-model-v3--router-incidents-modelled-the-way-networks-actually-fail)) | [Engineering decision 9](#9-why-cascade-is-folded-into-normal-during-training) |
| Live cluster validation | 16,600 requests (first 14 days of the test fleet, every machine every 4 hours), 0 errors, 100% served by v4, at the live threshold 0.91: F1 **0.7755** · precision 0.7202 · recall 0.84. A different sample from the full test file, so it is not comparable with the full-file F1. Measured on the 19 October model; not rerun on the 21 October retrain (offline F1 +0.0001) | `scripts/live_k8s_validation.py`, run on 19 October |
| Latest green pipeline | Build `2.20.3-b111` (commit `bd0fdd67`, 21 October), started automatically by the model pipeline after it promoted the retrained model: all 11 stages green, 179 tests, Quality Gate OK, model gate F1 0.7292 at threshold 0.91, 0 HIGH/CRITICAL image vulnerabilities, deployed and smoke-tested in the live pod (7 PASS). Before it: b109 (`20113d7b`: Fleet tab, Review details pop-up, MLflow Fix, failure model v3), after b108 was stopped by the Quality Gate (15 new issues, fixed) | [L3 run history](#l3--cicd) |
| Tests | 179 passing, in 18 files | `pytest tests/` (stage 1b) |
| Dashboard | 25 Grafana panels in 5 rows, provisioned from the repository | `monitoring/grafana/dashboards/devsecmlops-fleet.json` |
| Model provenance | Served since 2026-10-21: the model promoted by Jenkins `devsecmlops-model` build 9 (MLflow `d81b7ea6…`), trained on all 17,280,000 rows of the labelled fleet plus 315 operator-judged feedback rows (304 used for v3, 222 for v4, 11 skipped: unknown cause), v4 F1 0.7292, threshold 0.91 re-selected by the pipeline, guardrail PASS, model files read back from MinIO, committed by the pipeline (`bd0fdd67`) and deployed by the build it started (`2.20.3-b111`). Before that: the 19 October model (build 8, MLflow `241447d0…`, v4 F1 0.7291, image b103), the 13 October model (build 7, MLflow `f622a7a9…`, trained on 3.17M rows, v4 F1 0.7278, threshold 0.60), and the July models adopted on 2026-09-25 with hashes and an independent evaluation (v4 F1 0.7246) | [MLflow and MinIO](#mlflow-and-minio-experiment-tracking-artifact-store), [Known limitations](#known-limitations-and-future-work) |
| Deployment | Single-node Minikube, 2–5 replicas (HPA), demo and production-oriented Ansible profiles | [L4](#l4--kubernetes), [L6](#l6--ansible-infrastructure-as-code) |

---

## Executive summary

A production-oriented anomaly detection platform for a simulated 200-machine telecom fleet, built end-to-end across all seven layers (L0–L6), each independently verified live — not just built and assumed working.

The current numbers, build and deployment state are summarized in [At a glance](#at-a-glance).

**Every failure the CI/CD pipeline surfaced was diagnosed, fixed, and re-run until it passed** — five SonarQube Quality Gate blocks (runs 1, 2, 9, 15 and 17, proving `abortPipeline: true` genuinely stops the build), a Trivy vulnerability-database download timeout, a DNS failure between Jenkins and SonarQube caused by a real gap in the Ansible container definitions, a SonarQube server-side failure caused by the VM's disk reaching 95%, a Quality Gate block on new code (a duplicated literal in the chunked evaluation), and an image-scan block on 6 HIGH OpenSSL CVEs that Debian had already fixed but a stale cached build layer never picked up. The most recent run (build `2.20.3-b111`, started by the model pipeline after a promotion) passed every stage, with zero image vulnerabilities and a post-deploy smoke test through the live pod. See [L3](#l3--cicd) and [L6](#l6--ansible-infrastructure-as-code).

**What makes this project genuinely defensible, beyond the final metrics:**

- **Every layer independently verified live**, most more than once, with real command output as evidence — not "should work," but "here is the log showing it worked."
- **A long, honest list of real bugs found by actually running things**, not just reading code — see each layer's "Real bugs found and fixed" subsection. A non-exhaustive sample: a Dockerfile version pin silently installed completely unpinned because of an unquoted shell redirect; a benchmark script's row-subsampling silently collapsed to exactly one machine at real scale; a demo traffic generator could never assemble a real rolling-history window; an Ansible template drifted so far from the real Kubernetes manifest that a production deploy would have shipped an unhardened pod; three containers silently lost their shared Docker network after being recreated, breaking CI with no warning until the next build.
- **Twenty-four engineering decisions**, documented with real numbers, including several tested, rejected, and — in at least one case — later revisited and reversed on stronger evidence (see [L0](#l0--ml-model)).
- **Real-world validation**: the same per-machine, per-window z-score methodology, applied to the real, public Server Machine Dataset, yields results consistent with published unsupervised sequence-model literature on the same benchmark.
- **Infrastructure that survives a reboot**: every host service (Prometheus, the exporters, MLflow, the control panel) is a supervised systemd unit in both the demo and production Ansible profiles — closing a real gap where the demo profile's plain background processes died on a VM restart during this project's own development.

---

## Architecture

```
 L0  ML Model                 generator, baselines, v3/v4 classifiers, IsolationForest, guardrail
 L1  Serving API               FastAPI, hybrid v3/v4 routing, feedback loop, control panel
 L2  Container Image           Docker, hardened, model artifacts baked in
 L3  CI/CD                     Jenkins, 11 gates, SonarQube, Trivy, SBOM
 L4  Orchestration             Kubernetes (Minikube), hardened pods, HPA
 L5  Observability             Prometheus, Grafana, MLflow, test-set traffic replay
 L6  Infrastructure as Code    Ansible, two profiles, systemd-supervised
```

```
Git push -> Jenkins (SonarQube -> repo+model gate -> Docker build ->
             hardened smoke test -> Trivy image scan + SBOM -> registry push)
                                                  |
                                                  v
                                  Docker image (model baked in)
                                                  |
                                                  v
                           Kubernetes: FastAPI pods (2-5, autoscaled)
                                                  |
                   +------------------------------+------------------------+
                   v                                                       v
        Node Exporter (VM)                                     Kubernetes exporter
                   |                    production_agent.py               |
                   |                 (200 threads, real replay,           |
                   |                  real rolling history)               |
                   +------------------------------+------------------------+
                                                  v
                            Prometheus (per-pod discovery via the
                            Kubernetes API server -- not the NodePort)
                                                  |
                                                  v
                     Grafana (25 panels + 5 rows, provisioned)
```

Everything runs on a single self-hosted VM (Minikube) for the PFE. The image is built once and scanned once; moving to a multi-node cluster is a configuration change, not a code change: point `image:` at the registry Jenkins already pushes to and switch `imagePullPolicy` from `Never` (locally loaded images, the Minikube pattern used here) to `IfNotPresent`. That multi-node deployment has not been done in this project. Ansible brings the whole platform up from a bare provisioned VM in either profile (demo or production) — see [L6](#l6--ansible-infrastructure-as-code).

---

## Quick start

```bash
# required credentials (never committed -- see Security posture)
export GRAFANA_ADMIN_PASSWORD='...'   # 12+ chars
export OPS_UI_PASSWORD='...'          # 12+ chars
export OPS_UI_USER='admin'            # optional, defaults to "operator"
export MINIO_ROOT_USER='...'
export MINIO_ROOT_PASSWORD='...'      # 12+ chars

cd ansible
ansible-playbook -i inventory.ini site.yml                          # demo (default)
ansible-playbook -i inventory.ini site.yml -e profile=production --ask-vault-pass

# validate without changing anything
ansible-playbook -i inventory.ini site.yml --syntax-check
ansible-playbook -i inventory.ini site.yml --check
```

After a successful demo run, the control panel at `http://<vm-ip>:8000/ui` reports every platform layer's live status. Grafana is at `:3000`, Prometheus at `:9090`, Jenkins at `:8080`, SonarQube at `:9000`, MLflow at `:5001`.

---

## L0 — ML Model

Detects and explains anomalies: a synthetic generator, per-machine per-window baselines, two supervised classifiers (v3, v4) plus an unsupervised IsolationForest safety net, a guardrailed retraining pipeline, and the feedback loop that would eventually make retraining possible from real operator judgments rather than only synthetic data.

### How it works

**Training (offline, `train_production.py`, the single canonical path — see
[the ML model](#l0--ml-model)):** raw fleet data flows into baseline
construction (mean/std per machine and time window), gets z-scored using those
baselines, plus 9 rolling features for v4, and the classifiers plus the
IsolationForest fit on that data. Output: the artifacts listed in
[L0's file table](#files), fingerprinted and recorded in
`models/manifest.json`.

**Serving (live, every API call, milliseconds):** an incoming reading is
z-scored using the SAME saved baseline from training (never recomputed live),
fed into the frozen model(s), and returns `is_anomaly` plus the served decision
plus per-feature z-scores. If several machines flag as anomalous around the
same time, their results can be batched into `/root-cause`, which uses the
dependency graph to rank them by root-cause likelihood.

Baselines and z-scores are not the same thing: the baseline is two saved
numbers (mean, std) per machine and time window. The z-score is a calculation
done fresh every time using that baseline — `(raw - mean) / std` — both
during training and during every prediction. This equivalence — training-time
and serving-time feature computation being the literal same function, not two
implementations kept in sync by convention — is enforced by import
(`ml-model/preprocess.py` is imported directly by `api/app.py`) and checked by
test (`tests/test_api.py::test_rolling_features_match_training_pipeline`).

### What we tested (the current, shipped design)

#### Why per-machine, per-time-window baselines

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

#### Why 6 features

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

#### Data resolution: 1-minute vs 30-second (tested, adopted)

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

#### Why unsupervised, not supervised

A supervised classifier (Random Forest, XGBoost, etc.) would score higher F1
on this labeled synthetic dataset, but it needs labeled anomalies to train.
Production infrastructure has none — nobody manually labels every anomalous
minute across 200 servers. Isolation Forest learns "normal" from the incoming
metric stream with zero labels, and can flag anomaly *types* it has never
seen before. The dataset's `label` column is used only to evaluate the model,
never to train it. (The project did add a supervised layer on top later — see
"ML model v2" below — but kept Isolation Forest as a permanent safety net for
exactly this reason: it is the one part of the design that needs no labels at
all, and so is the one part that would already work on a genuinely
unlabeled, real deployment today.)

#### Why Isolation Forest (updated — corrected methodology)

Six anomaly-detection methods have been benchmarked on the same data,
`ml-model/benchmark.py`. **The methodology this comparison uses was corrected
in this project's most recent review pass, and the numbers below reflect
that correction, not a model change.** Two real bugs were found in the
original benchmark script: the train/test split was a random shuffle rather
than time-ordered per machine (letting a model see rows chronologically
*after* ones it was tested on, for the same machine — reproduced directly
with a 6-row example before fixing), and point-adjustment (crediting a whole
contiguous anomaly block as detected if any reading in it was flagged) was
computed across the whole shuffled array rather than per machine, which could
let a detection on one machine wrongly credit a block belonging to a
*different* machine. Both are fixed. The table below is from a real run of
the corrected script against the full, independent 17,280,000-row test set
(200 machines, 6.74% anomaly rate, ~36 minutes wall time) — not a subsample,
and not the buggy split this section originally reported:

| Model | Precision | Recall | F1 | F1@adj | ROC-AUC | PR-AUC | PR-AUC@adj |
|-------|-----------|--------|-----|--------|---------|--------|------------|
| z-threshold (\|z\|>3) | 0.736 | 0.587 | 0.653 | 0.797 | 0.903 | 0.689 | 0.860 |
| Isolation Forest, raw (no z-score) | 0.334 | 0.272 | 0.300 | 0.436 | 0.756 | 0.318 | 0.487 |
| **Isolation Forest, z-scored (shipped)** | 0.744 | 0.588 | 0.657 | 0.809 | **0.920** | 0.710 | 0.884 |
| Isolation Forest, robust z-score (trimmed baselines) | 0.771 | 0.575 | **0.659** | **0.812** | 0.908 | 0.703 | 0.875 |
| OneClassSVM, z-scored | 0.551 | 0.552 | 0.551 | 0.735 | 0.894 | 0.582 | 0.868 |
| Local Outlier Factor, z-scored | 0.230 | 0.217 | 0.223 | 0.619 | 0.685 | 0.174 | 0.724 |
| Autoencoder, z-scored | 0.269 | 0.202 | 0.231 | 0.665 | 0.729 | 0.190 | 0.730 |

F1@adj = point-adjusted F1 (OmniAnomaly/SMD standard, computed correctly
per-machine as described above): a single correctly flagged point inside an
anomaly block counts the whole block as detected — the metric commonly
reported in time-series anomaly detection research.

**Robust z-scoring** (trimmed/winsorized baselines) is, under this corrected
methodology, marginally *ahead* of standard z-scoring (F1 0.659 vs 0.657,
F1@adj 0.812 vs 0.809) — the reverse ordering from what an earlier, buggier
run of this same comparison reported. The conclusion is unchanged despite the
flip: the two are close enough to be statistically indistinguishable on this
data, so the simpler standard z-score remains shipped rather than switching
to the marginally-ahead-but-more-complex robust variant. This flip is
recorded here deliberately, as a concrete illustration of why the split/
point-adjustment bugs mattered: they didn't just shift numbers, they could
flip which of two close options looked better.

Honest reading of this table: the simple z-threshold rule is competitive with
Isolation Forest on strict F1 (0.653 vs 0.657 — much closer than an earlier
run of this comparison suggested). Isolation Forest is still shipped, for
three reasons a single F1 number does not capture:

1. **Better ranking quality.** ROC-AUC (0.920 vs 0.903) favors Isolation
   Forest — it ranks anomalies more reliably across all possible thresholds,
   not just the one z-threshold happens to use.
2. **Continuous, tunable score.** z-threshold is a fixed per-feature cutoff
   (exactly 3 sigma). Isolation Forest exposes a continuous anomaly score
   (in the API response as `iso_score`) that operators can re-tune via
   `contamination` without code changes.
3. **Multivariate detection.** z-threshold flags a reading if ANY single
   feature exceeds 3 sigma. It cannot catch a case where four features are
   each moderately elevated but jointly represent a real incident — a
   structural gap the per-feature rule cannot close.

Isolation Forest also requires no labeled anomalies (production has none),
infers in well under the API's real request latency, needs only one key
hyperparameter (`contamination`), and scales cleanly to 200+ machines.
Comparing "raw" vs "z-scored" in the table shows the z-score normalization
step is what actually drives its performance — raw F1 0.300, z-scored F1
0.657, more than double.

### What we tried and rejected, and how the model evolved (v1 -> v4)

Real, sequential experiments across this project's development, kept because a rejected approach with real numbers is as valuable as an adopted one.

#### Cascading failures and root cause analysis

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


#### ML model v2: labeled cause classification (July 2026)

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

**Two models, evaluated on the independent test set at the time:**

| Model | F1 | Precision | Recall |
|---|---|---|---|
| **RandomForest (supervised, primary)** | **0.731** | 0.849 | 0.641 |
| Isolation Forest (unsupervised, safety net) | 0.652 | 0.655 | 0.649 |

Per-cause recall, RandomForest: cpu_spike 0.858, memory_leak 0.688,
network_flood 0.881, disk_saturation 0.965, silent_failure 0.868,
cascade 0.029.

> **These are the real numbers from this stage of the project.** They predate
> the v3/v4 switch, the threshold correction, and the served-decision-rule
> discipline this document now enforces throughout. Do not read `0.731` as a
> current production number — the current one is 0.7292 served (v4,
> threshold 0.91), stated in [Executive summary](#executive-summary) and
> [Current production](#current-production-the-numbers-that-supersede-everything-above) below.

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
therefore runs both — this remains true today: the served decision is
still `classifier OR IsolationForest`, RandomForest since replaced by
XGBoost (v3) and then the rolling-feature v4, but the dual-model principle
that made this evaluation necessary has not changed.

**Two architectural fixes attempted and rejected, with evidence:**

| Attempt | F1 | Why it failed |
|---|---|---|
| Blind ensemble (flag if either model fires) | 0.663 | Inherited Isolation Forest's false positives on top of RandomForest's correct calls. Judged on F1 in the RandomForest era; the shipped rule is this same OR, kept for recall. Measured trade-off today: [Decision 10](#10-why-dual-model-architecture-primary--safety-net) |
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
rather than single-timestamp z-scores to detect well -- and this is
exactly the direction the v4 rolling-feature work below took, on the
production generator's own (instant-onset) data rather than this
gradual-onset variant.

**A fifth experiment tested a concrete step toward that sequence-aware
direction: rolling/trend features.** Per-machine rolling mean, rolling
std, and delta (rate of change) over the last 10 readings (5 minutes)
were added for cpu/ram/load_avg, on top of the existing z-scored
features, and RandomForest was retrained on the same 5-day pilot split
used for the cascade-folding experiments above.

| Metric | Baseline (z-score only) | With rolling features |
|---|---|---|
| F1 | 0.708 | **0.729** |
| Precision | 0.939 | **0.978** |
| Recall | 0.568 | 0.581 |

A real, honest improvement -- precision rose meaningfully and F1 by
2.1 points. memory_leak recall (the original motivation) improved
only modestly (0.573 -> 0.606); cpu_spike recall dropped in this run
(0.751), suggesting the added features shift the model's attention
across categories rather than uniformly helping.

This pilot result is what later grew into the full v4 model (see below) --
at full scale, the gain was far larger than this 5-day pilot suggested.

**Real-world validation on SMD (Server Machine Dataset):** the same
per-machine, per-time-window z-score methodology was applied unmodified to
SMD — 28 real servers, 708K rows, 4.16% anomaly rate, the public benchmark
used in Su et al. (KDD 2019, OmniAnomaly). Three variants tested:

| Variant | Features | F1 |
|---|---|---|
| Per-machine, no window | 37 (all) | 0.248 |
| **Per-machine + time window** | 37 (all) | **0.269** |
| Top-3 variance features only | 3 | 0.159 |

F1=0.269 is far lower than the synthetic-data results, and that gap is the
finding. It is not directly comparable with published SMD numbers (for example
OmniAnomaly, KDD 2019): those papers use recurrent models that read a *sliding
window* of recent history, and they usually score with *point-adjusted* F1
(one detected point credits the whole anomalous segment), which is far more
lenient than the per-reading F1 used here. An earlier version of this text
quoted a "0.40–0.55" literature range without a citation; it was removed
rather than defended. This
project prioritizes training speed, interpretability, and straightforward
CI/CD-integrated deployment over the marginal accuracy gains of a
recurrent sequence model — a deliberate, documented tradeoff, not an
oversight. The SMD result's purpose is not to win on the leaderboard; it
shows the methodology transfers to real, independently-collected data at
reduced accuracy (F1 0.269), so the synthetic-data scores must not be read as
real-world accuracy. `ml-model/load_smd.py`
is the adapter for this dataset; it remains in the repository, still fully
functional, and was not touched by the v4/threshold work described later
in this document.

**Model artifacts:** `models/telecom_rf_classifier_v2.pkl` (RandomForest,
not committed — 125MB exceeds GitHub's 100MB limit, fully reproducible via
`generate_telecom_fleet.py --seed 42` + the training pipeline,
`random_state=42`, deterministic), `models/telecom_iso_v2.pkl`
(IsolationForest, committed), `models/telecom_baselines_v2.json`.

#### ML model v3: XGBoost primary (July 2026)

After the v2 evaluation, a sixth model comparison was run: XGBoost as a
potential replacement for the RandomForest primary. Trained on the same
subsampled full-scale data (3.17M rows: all anomalies + 2M normal, seed 42),
evaluated on the same independent seed-123 test set, same methodology as v2
in every respect except the classifier itself.

**Offline results (identical test set as v2, numbers from this stage):**

| Model | F1 | Precision | Recall | Model size | Train time |
|---|---|---|---|---|---|
| v2 RandomForest | **0.731** | **0.849** | 0.641 | 125MB | ~340s |
| v3 XGBoost | 0.718 | 0.775 | **0.670** | 3.6MB | 74s |

Overall F1 favors RandomForest by 1.3 points and precision by 7.4 points,
but XGBoost **wins per-cause recall on every single anomaly type**:
cpu_spike 0.858 -> 0.894, memory_leak 0.688 -> 0.736 (a real improvement
on the previously weakest category), network_flood 0.881 -> 0.915,
disk_saturation 0.965 -> 0.977, silent_failure 0.868 -> 0.906, cascade
0.029 -> 0.042 (still weak but relatively better). XGBoost is more
aggressive: catches more real anomalies at the cost of more false
positives on the aggregate mix.

**Live K8s validation at the time (33,600-request two-week demo):**

| Metric | v2 RF (live) | v3 XGBoost (live) |
|---|---|---|
| F1 | 0.699 | 0.689 |
| Precision | 0.683 | 0.647 |
| Recall | 0.716 | **0.738** |
| Cause accuracy | 90.2% | **90.5%** |
| p50 latency | 214ms | **128ms** |
| Throughput | 40 req/s | **103 req/s** (2.5x) |

**Decision at the time: switch production to v3.** Reasoning:
1. **Better per-cause recall on every category** — this is what matters
   operationally in a telecom monitoring context. Missing a real memory
   leak or cascade costs more than an extra false-alarm investigation.
2. **34x smaller model** (3.6MB vs 125MB) — eliminates the whole
   MinIO-fetch-at-startup architecture built for v2. Model is now baked
   directly into the Docker image, same simple pattern as v1.
3. **4.6x faster training** — genuinely easier to iterate/retrain.
4. **Live throughput 2.5x higher, p50 latency ~40% lower** — real
   operational advantages under load.

v2 code paths (MODEL_NAME=telecom_v2, MinIO-fetch entrypoint) are kept
intact for reproducibility and defense-day A/B comparison purposes --
this is an additive switch, not a destructive one. Setting
MODEL_NAME=telecom_v2 in the deployment env still fully works.

**Model artifacts:** `models/telecom_xgb_classifier_v2.pkl` (XGBoost,
3.6MB, committed), `models/telecom_xgb_label_encoder_v2.pkl` (559
bytes, committed), plus `models/telecom_iso_v2.pkl` and
`models/telecom_baselines_v2.json` shared with v2. All four baked into
the Docker image directly -- no runtime MinIO fetch needed.

**LightGBM also tested for completeness** (same methodology, same data,
same evaluation set): F1=0.716, Precision=0.767, Recall=0.671 -- within
0.002 of XGBoost on every metric, essentially identical per-cause recall
numbers, 3.0MB model, 51s training. This is a genuinely useful negative
result: it shows the ceiling on this data with these features is a
gradient-boosting-*family* ceiling, not an XGBoost-specific one. LightGBM
artifacts kept in the repo as evidence but not adopted, since it offers no
real advantage over the already-deployed XGBoost.

> **v3, today:** the numbers above are what justified the v2→v3 switch at
> the time. v3 is still shipped, as the fallback path whenever a request
> has no rolling history — its current, corrected served F1 (independent
> seed-123 test set, threshold 0.91) is **0.6667**, stated in
> [Executive summary](#executive-summary). See "v4" immediately below for what replaced it as
> the primary path.

#### Rolling features and gradual-onset detection (v4)

The v3 model looks at ONE reading in isolation: is this cpu/ram/etc snapshot anomalous right now? This works for sudden spikes but struggles with **gradual-onset** anomalies -- a memory leak where ram climbs slowly from 60% to 95% over several minutes. At any single moment the reading looks only mildly elevated; the anomaly is in the *trend*, not the *value*.

v4 adds 9 rolling/trend features on top of the 6 z-scored base features (15 total), computed per machine over the last 10 readings (5 minutes at 30-second resolution):

- `{cpu,ram,load_avg}_rolling_mean` -- recent baseline
- `{cpu,ram,load_avg}_rolling_std` -- recent volatility
- `{cpu,ram,load_avg}_delta` -- rate of change (current minus 10-readings-ago)

Applied only to metrics where change-over-time is diagnostic. disk_usage (accumulates monotonically), disk_io (already noisy), and network (spikes are normal) are excluded.

##### Offline results at the time (full-scale, same seed-123 test set as v3)

| Model | F1 | Precision | Recall |
|---|---|---|---|
| v3 (6 features) | 0.718 | 0.775 | 0.670 |
| v4 (15 features), threshold 0.5 | 0.816 | 0.931 | 0.726 |

+0.098 F1, +0.156 precision, +0.056 recall at the time -- a large, real improvement. Per-cause recall improved on every category except cascade (which stays limited by the 40%-consistent label noise documented above): cpu_spike 0.894 -> 0.982, memory_leak 0.736 -> 0.909 (the +17-point gradual-onset win), network_flood 0.915 -> 0.976, disk_saturation 0.977 -> 0.994, silent_failure 0.906 -> 0.978.

> **This F1=0.816 figure is the classifier alone, at threshold 0.5 — not the served decision, and not at the threshold now deployed.** It is real and it is kept here because it is genuinely informative about the classifier's own strength. The number this project now reports as "production" is the *served* decision (classifier OR IsolationForest) at the threshold the pipeline selected (0.91): **F1 = 0.7291**. See [Executive summary](#executive-summary) and [Current production](#current-production-the-numbers-that-supersede-everything-above) for the full, current, corrected numbers and the reasoning behind every difference from this section.

##### Live K8s results at the time (33,600-request two-week demo, threshold 0.85)

| Config | F1 | Precision | Recall | Cause acc |
|---|---|---|---|---|
| v3 @ 0.85 | 0.576 | 0.452 | 0.793 | 90.0% |
| v4 @ 0.85 | 0.690 | 0.597 | 0.817 | 91.9% |

v4 beat v3 on every live metric at the time, zero errors on 33,600 requests. memory_leak recall live: 77.9% -> 95.4%.

> **The 0.85 threshold used here came from a sweep of the classifier alone and
> was never re-validated on the served decision** — see [Engineering decision 20](#20-why-the-decision-threshold-is-selected-by-the-pipeline-not-fixed-revised-twice).
> A proper F2-maximizing sweep on an independent validation fleet selected
> **0.60** instead. A fresh live validation at the corrected threshold, against
> the real deployed cluster, is in [Current production](#current-production-the-numbers-that-supersede-everything-above):
> on the 19 October model (threshold 0.91) F1 = 0.7755 on 16,600 live HTTP requests (carrying simulated readings), 0 errors, every one served by v4.

##### Serving architecture: client supplies history (stateless API)

Rolling features need the last 10 readings, but the API is stateless (each /predict is independent, replicas share no state). Rather than add per-machine buffers in the pods (needs Redis / cross-replica sync) or query Prometheus on every call (couples API to Prometheus, doubles latency), v4 uses the pattern real monitoring systems use: **the client supplies the history**.

The /predict request gains an optional `history` field:

```json
{
  "machine": "web-01",
  "metrics": {"cpu": 45, "ram": 95, ...},
  "history": {
    "cpu":      [40, 42, 44, 45, 45, 46, 45, 44, 45, 45],
    "ram":      [60, 64, 70, 76, 82, 86, 90, 92, 94, 95],
    "load_avg": [1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.0, 2.0, 2.0, 2.0]
  }
}
```

**Hybrid routing**: when `history` is present and the v4 model is loaded, the API computes the 9 rolling features and uses the 15-feature v4 model (response shows `model: telecom_v4_rolling`). When `history` is absent or malformed, it falls back to the 6-feature v3 model. Same endpoint, two modes, backward compatible. The IsolationForest safety net runs on the base 6 features in both paths. As of the most recent API hardening pass, `history` is also validated strictly: it must contain exactly `cpu`/`ram`/`load_avg`, each with exactly 10 finite values — the same window shape v4 was trained on, rejected otherwise rather than silently computing rolling features on a differently-shaped window.

The serving-side rolling-feature computation is verified BYTE-IDENTICAL to the training-time function (`ml-model/preprocess.add_rolling_features`) on the same input -- a mismatch would feed the model garbage features, so this equivalence is explicitly tested (`tests/test_api.py::test_rolling_features_match_training_pipeline`).

The traffic source that actually builds this history for every machine, continuously, is `monitoring/production_agent.py` — see [L5 — Observability](#l5--observability-and-mlops) for its full design, including a real bug (random-row sampling that could never assemble a genuine history window) found and fixed during the most recent review.

##### Why v4 is additive, not a replacement

v3 remains fully functional and is the fallback whenever history isn't available. This means the system degrades gracefully: a client that can't supply history still gets v3-quality predictions rather than an error. Both models are baked into the image; the routing is per-request.

##### Per-class thresholds and the master comparison

The single-threshold rule (flag if P(normal) < T) applies one cutoff to every
cause. But causes differ: `cascade` is rare and weak (needs a low bar),
`disk_saturation` is clean and strong (can afford a high bar). **Per-class
thresholds** flag an anomaly if ANY non-normal class probability exceeds that
class's own threshold. These are opt-in via the `PER_CLASS_THRESHOLDS` env var
(a JSON map); absent, the single-threshold behavior — now selected by the F2
sweep described in [Engineering decision 20](#20-why-the-decision-threshold-is-selected-by-the-pipeline-not-fixed-revised-twice) —
applies.

To find the best configuration available under the model as it stood at the
time, a single controlled experiment (`scripts/master_comparison.py`, still
present in the repository and independent of the later threshold-selection
work) tested **every model × every threshold × with/without feedback** — 12
configurations — on the same seed-123 test set. Per-class thresholds were
tuned on a validation half of the test set and reported on the held-out half,
so the per-class numbers are not overfit. XGBoost-only metrics (the
IsolationForest safety net is unsupervised and unaffected by feedback):

| Config | F1 | Prec | Recall | mem_rec | casc_rec |
|---|---|---|---|---|---|
| v3 base · single@0.5 | 0.717 | 0.770 | 0.671 | 0.738 | 0.042 |
| v3 base · single@0.85 | 0.555 | 0.444 | 0.739 | 0.858 | 0.133 |
| v3 base · per-class | 0.756 | 0.849 | 0.682 | 0.648 | 0.185 |
| v3 +feedback · single@0.5 | 0.708 | 0.734 | 0.683 | 0.757 | 0.059 |
| v3 +feedback · single@0.85 | 0.516 | 0.363 | 0.893 | 0.886 | 0.696 |
| v3 +feedback · per-class | 0.756 | 0.852 | 0.679 | 0.642 | 0.179 |
| v4 base · single@0.5 | 0.815 | 0.929 | 0.726 | 0.910 | 0.017 |
| v4 base · single@0.85 | 0.748 | 0.746 | 0.749 | 0.964 | 0.050 |
| **v4 base · per-class** | **0.823** | **0.953** | 0.725 | 0.868 | 0.040 |
| v4 +feedback · single@0.5 | 0.811 | 0.912 | 0.730 | 0.912 | 0.026 |
| v4 +feedback · single@0.85 | 0.709 | 0.572 | 0.933 | 0.970 | 0.763 |
| v4 +feedback · per-class | 0.822 | 0.950 | 0.724 | 0.864 | 0.041 |

**Three conclusions from this experiment, still valid:**

1. **v4 dominates v3 in every column** — rolling features are the single
   largest lever. Settled, and unaffected by anything in the later
   threshold-selection work (that work only changed *which single number*
   is used as the cutoff, not this comparison).
2. **Best per-class F1 at the time = v4 base + per-class thresholds (F1
   0.823, precision 0.953)** — the highest F1 in this specific experiment,
   honestly measured on the held-out half. Per-class thresholding was not
   the direction the later, corrected threshold-selection work took (that
   work selects one number by F2 on a genuinely separate validation fleet
   — see [Engineering decision 20](#20-why-the-decision-threshold-is-selected-by-the-pipeline-not-fixed-revised-twice)),
   but the finding that per-class thresholds *can* beat a single threshold
   on this data is real and kept here as a documented, viable alternative
   design not currently shipped.
3. **Feedback does not raise balanced F1** at demonstration scale (≤1.55% of
   the training signal) — every `+feedback` row equals or slightly trails its
   `base` equivalent on F1. BUT feedback is not useless: at the recall-priority
   threshold (0.85) it sharply increases recall and cascade detection
   (**v4 +feedback @0.85: recall 0.933, cascade_rec 0.050 → 0.763**). This is
   consistent with, and a more detailed version of, the same finding recorded
   in [Engineering decision 24](#24-why-the-retrain-guardrail-is-strict-and-what-500-rows-proved-updated)
   about the 500-row retrain test.

This experiment predates, and is independent of, the current single-threshold
selection methodology — both are real, and the honest relationship between
them is: per-class thresholding was explored and found to work well but was
not the direction shipped; a single, evidence-selected threshold (0.60, by
F2) is what production actually runs today.

##### Live serving validation: offline metrics vs the real API (at the time)

The master-comparison numbers above are **offline, XGBoost-only** — the model
scored directly on the test matrix. To check they held through the *actual
serving path* at the time, the real FastAPI app was run locally and sent
**8,000 live HTTP requests per configuration** (each with rolling history, so
~99% took the v4 path), scoring the responses exactly as production would.
This exercises z-scoring, rolling-feature computation, hybrid routing, the
threshold logic, AND the IsolationForest safety net's OR-gate — the full dual
model, not just XGBoost.

| Config (live, 8,000 requests, at the time) | F1 | Precision | Recall | mem_leak | cascade |
|---|---|---|---|---|---|
| v4 single @0.85 | 0.696 | 0.607 | 0.816 | 0.938 | 0.246 |
| v4 single @0.50 | 0.750 | 0.713 | 0.792 | 0.887 | 0.198 |
| v4 per-class (F1-optimal) | 0.750 | 0.720 | 0.783 | 0.825 | 0.214 |

**The key finding from this experiment, still true in principle:** an
offline threshold-strategy advantage can compress once the live system's
IsolationForest safety net is in the loop, because the safety net fires
independently of whatever XGBoost threshold strategy is in effect, adding
roughly the same false positives regardless of the strategy chosen. This is
part of why the later threshold-selection work measures the **served**
decision (classifier OR IsolationForest) directly, at every candidate
threshold, rather than tuning the classifier alone and hoping the live
system inherits the same advantage — see
[Choosing the decision threshold](#choosing-the-decision-threshold)
below, which is the methodology now used, precisely because of what this
experiment found.

A further, more recent live-serving validation — against the actual
deployed Kubernetes cluster, at the corrected threshold, on the independent
test set — is in [Current production](#current-production-the-numbers-that-supersede-everything-above): 16,600 live
HTTP requests (simulated readings), 0 errors, F1 0.7755 (19 October model).


### Current production (the numbers that supersede everything above)

This section is the single source of truth for every number this document
calls "current" or "production" elsewhere — the endpoint of the v1→v2→v3→v4
history above, after the most recent correction pass.

#### The canonical training path

A single script, `ml-model/train_production.py`, is now the only route to a
promoted production model — it superseded the separate, earlier
`scripts/retrain_from_feedback.py`. Given a training set and a test set, it
fingerprints both (SHA-256), trains baselines + v3 + v4 + IsolationForest,
evaluates the **candidate and the current production models with the
identical served decision rule** (`ml-model/decision.py` — `classifier_vote
OR isolation_forest_vote`, imported directly by both the API and every
evaluation script, so a reported number is guaranteed to match what the
deployment actually returns), applies the guardrail described in
[Engineering decision 24](#24-why-the-retrain-guardrail-is-strict-and-what-500-rows-proved-updated),
and — only with `--promote` and only if the guardrail passes — writes
artifacts and records full lineage in `models/manifest.json`.

With `--promote`, the candidate first gets **its own** decision threshold: it is selected on the independent validation fleet (seed 7), by the rule in `config/model_policy.json` (currently: the highest precision whose served recall is at least 0.80; F-beta when no recall target is set), for exactly the candidate's artifacts (the selection record carries their SHA-256). The candidate is then evaluated at that threshold and production at its own recorded one, so the guardrail compares each model at its own operating point. With `--promote` that comparison is made on the validation fleet, so the test set that supplies the headline score does not also decide promotion (without `--promote` the verdict is a preview on the test set); the manifest history records `guardrail_set`. This is deliberate: a threshold is a setting of one particular model, so judging a new model at the old model's number would measure the wrong thing, and each history entry records both thresholds and the validation file the candidate's was selected on. In `models/manifest.json` history, `promoted_to_workspace` means the guardrail approved the candidate and its artifacts were written to the working tree; the `production` entry that CI deploys changes only after the CI model gate passes and `Jenkinsfile.model` commits it. `--promote` refuses to run without a validation fleet, and refuses one that is byte-identical to the train or test file; so does `select_threshold.py`, so evidence can never be written from the test set. `train_production.py --promote` is the only path that creates a new production model and its threshold. `select_threshold.py --record` only re-selects the threshold for the artifacts already in production (the record binds their SHA-256) and clears the recorded evaluation, so the CI gate fails until `train_production.py --evaluate-production` has redone it. This is enforced: `--record` refuses any `--models-dir` other than `models/`.

#### Choosing the decision threshold

> **History.** The sweep below was measured on the 13 October model and selected 0.60 by maximizing F2. Since 19 October the pipeline chooses the threshold itself, per trained model, by the rule in `config/model_policy.json`; see [Current method](#current-method-one-policy-file-a-threshold-chosen-per-model) after the table. The table is kept as the record of the earlier method.

v3/v4 flag an anomaly when `P(normal)` falls below a threshold. As explained
in [Engineering decision 20](#20-why-the-decision-threshold-is-selected-by-the-pipeline-not-fixed-revised-twice),
this project's threshold was, until this pass, **0.85 — chosen from a sweep
of the classifier alone, never re-validated on the served decision**. It has been
reselected by method: `ml-model/select_threshold.py` sweeps 0.05–0.95 on a
**validation fleet** (seed 7 — separate from both the seed-42 training set
and the seed-123 test set), computing the served decision at every
threshold, and selecting the one maximizing **F2** (recall weighted twice
precision). Full sweep, 8,064,000 rows:

| Threshold | Precision | Recall | F1 | F2 |
|---|---|---|---|---|
| 0.50 | 0.6733 | 0.7962 | 0.7296 | 0.7682 |
| **0.60 (selected)** | **0.6632** | **0.8005** | **0.7254** | **0.7687** |
| 0.70 | 0.6470 | 0.8048 | 0.7173 | 0.7674 |
| 0.85 (old default) | 0.5924 | 0.8133 | 0.6855 | 0.7568 |
| 0.95 | 0.4599 | 0.8258 | 0.5908 | 0.7124 |

The evidence, and the full curve, is recorded in `models/manifest.json`
under `production.threshold_selection`. A CI gate
(`scripts/verify_model_manifest.py`) enforces that the threshold actually
deployed in every Kubernetes manifest, every Ansible template, and the API's
own default all agree with the one the evaluation was measured at. (Since 19 October the gate instead requires that no deployment file carries a copy at all.)

#### Current method: one policy file, a threshold chosen per model

Everything a person may want to change about training and thresholds lives in **`config/model_policy.json`**; no other file is edited.

| Key | Value now | Meaning |
|---|---|---|
| `training.normal_sample` | `null` | Train on every normal row (17,280,000 rows: 1,165,947 anomalies + 16,114,053 normal); a number would sample that many normal rows |
| `threshold.min_recall` | `0.80` | Choose the highest-precision threshold whose served recall on the validation fleet is at least this; `null` falls back to F-beta |
| `threshold.beta` | `3.0` | F-beta weight, used only when `min_recall` is `null` or unreachable |
| `threshold.grid` | 0.05 to 0.99, step 0.01 | Thresholds tried |
| `guardrail` | max F1 drop 0.03, max recall drop 0.02 | A candidate that is worse than production by more than this is not promoted |

How it flows: the training job reads the policy, trains, evaluates the served decision (classifier OR IsolationForest) on the validation fleet over the grid, picks the threshold by the rule, evaluates the candidate at that threshold and production at its own, and records the choice, the rule and the policy in `models/manifest.json`. The API reads `production.decision_threshold` from that manifest when it starts; the environment variable `PREDICT_THRESHOLD` remains only as an explicit override, and `/health` → `model_lineage` reports the value and its source. The CI model gate fails if any deployment file hard-codes a threshold. Deployment stage 8 removes a stale `PREDICT_THRESHOLD` from the live Deployment before each rollout, and the smoke test fails unless the live threshold comes from the manifest (both added after an old environment variable was found overriding the manifest on 19 October).

| | 13 October (3.17M rows, fixed rule F2) | 19 October (all 17.28M rows, recall target) |
|---|---|---|
| Threshold | 0.60 | 0.91 |
| v4 precision / recall / F1 (test) | 0.6717 / 0.7941 / 0.7278 | 0.6713 / 0.7979 / 0.7291 |
| v3 F1 (test) | 0.6550 | 0.6664 |
| Validation fleet at the chosen threshold | – | recall 0.8008, precision 0.6712, F1 0.7303 |

Reading it honestly: training on all rows raised the share of normal rows from about 63% to 93%, which shifts the classifier's probabilities, so the best threshold moved from 0.60 to 0.91. The result is about the same operating point as before: recall +0.004, F1 +0.001 (one seed, within noise), so no improvement is claimed. The recall target was met on the validation fleet (0.8008) and is 0.7979 on the test fleet. Recall cannot reach "never miss one": about one anomalous reading in five is still missed, `cascade` in particular, and the IsolationForest carries most of the recall, so moving the threshold changes it little. Recall here is per reading, not per incident.

**21 October (build 9).** The same policy, retrained with 315 judged feedback rows: threshold 0.91 again (validation recall 0.801, precision 0.6713, F1 0.7304), test v4 0.6713 / 0.7979 / 0.7292, v3 0.6667. A change of +0.0001: no improvement is claimed, and none was expected from 222 rows among 17.28M.

#### Production numbers (independent test set, 17,280,000 rows, threshold 0.91)

| Served path | Precision | Recall | F1 | Classifier-only F1 |
|---|---|---|---|---|
| **v4 (production)** | 0.6713 | 0.7979 | **0.7292** | 0.8102 |
| v3 (fallback, no history) | 0.5971 | 0.7545 | 0.6667 | 0.7124 |
| v4 @ threshold 0.5, for reference (19 October model) | 0.6956 | 0.7743 | 0.7329 | 0.8192 |

Per-cause recall (v4, served decision, threshold 0.91, read from `models/manifest.json`): `cpu_spike` 0.988, `disk_saturation` 0.9967, `memory_leak` 0.9333, `network_flood` 0.9945, `silent_failure` 0.9811, `cascade` 0.268. The v3 fallback catches cascades slightly better (0.2989). Both classifiers are trained with cascade labelled `normal`, so cascade detection comes almost entirely from the IsolationForest safety net (see
[Engineering decision 9](#9-why-cascade-is-folded-into-normal-during-training)
for why cascade remains the one weak point — the generator's own cascade
labeling is inconsistent between rows, unchanged since that decision was
first made). Cause accuracy on detected, correctly-named anomalies: 0.9983.

**On the number `0.816`, and on `0.85`, which circulated in earlier versions
of this document:** `0.816` is the classifier's own F1 at threshold 0.5, in
isolation — real, but never the served decision at any threshold. `0.85` was
never the product of the search above; it came from an earlier sweep of the
classifier alone, before the served decision (classifier OR IsolationForest)
was measured. `0.7292` at threshold `0.91` is what a request to this
deployment actually returns today.

#### Live validation against the real, running cluster

`scripts/live_k8s_validation.py` sends live HTTP requests (carrying simulated readings) through the NodePort to
the running deployment, sampling every machine every 4 hours across the
independent test set, each with its own preceding 10 readings from the test file as
history, reporting which model actually answered rather than assuming:

```json
{
  "requests": 16600, "errors": 0,
  "served_by_model": {"telecom_v4_rolling": 16600},
  "f1": 0.7755, "precision": 0.7202, "recall": 0.84,
  "cause_accuracy_on_detected": 0.9977
}
```

This is the run of 19 October 2026 against the model promoted that day; it was not rerun on the 21 October retrain, whose offline F1 differs by +0.0001 (cluster threshold 0.91, read from the manifest and confirmed on `/health` right after the run): the first 14 days of the independent test file, every machine every 4 hours, 16,600 requests, all answered by v4, 0 errors. Per-cause recall: `cpu_spike` 0.9676, `disk_saturation` 0.9867, `memory_leak` 0.949, `network_flood` 0.9944, `silent_failure` 0.9721, `cascade` 0.2598. Latency through the NodePort with 20 concurrent workers: p50 308 ms, p95 699 ms, maximum 1,413 ms (a load-test figure, not single-request latency). **The F1 of 0.7755 is higher than the 0.7291 measured on the whole test file for that model because it is a different sample (14 days, one reading in 480 per machine (every 4 hours at 30-second resolution)), not because the served model is better; the offline figure on all 17.28M rows is the one to quote.** The earlier live run (13 October model, threshold 0.60, 2,200 requests: F1 0.7267, precision 0.6229, recall 0.872) used a smaller sample and is likewise not comparable. This supersedes the earlier 33,600-request live validation reported
in the "Rolling features (v4)" section above, which used the unmeasured 0.85
threshold and, in an even earlier iteration of this project, was measured
against data that overlapped the training set — that overlap has since been
corrected (this script now reads only the seed-123 independent file, with
its SHA-256 recorded in the output specifically so the claim is checkable),
and this run is the current, trustworthy record.


#### Model integrity

`models/manifest.json` (schema 2) records, for the current production
model: every artifact's SHA-256, the datasets it was trained and evaluated
on (also fingerprinted), the selected threshold and its evidence, and the
full evaluation. Both the API (before unpickling any model file —
unpickling executes code) and CI verify these hashes match before doing
anything else. A model file that differs from what the manifest promoted is
refused, not loaded.


### Feedback loop and retraining

The core limitation of every model version up to v2.11.1 is that the model is **static**: once trained on the synthetic seed-42 data, it never improves regardless of what happens in production. If the model flags 500 false alarms in a month and operators mark them all as fake, the model keeps making the same 500 false alarms next month.

This section documents the feedback loop that changes that -- an infrastructure for the model to learn over time from real operator judgments about its predictions.

#### Feedback-loop architecture

Every prediction the API makes gets stored in a persistent database. Operators can submit verdicts on those predictions (true positive, false positive, true negative, false negative). A retrain pipeline periodically combines this labeled feedback with the original training data to produce an improved model, with guardrails that prevent deploying a worse model.

Four independent pieces:

1. **Prediction logging** -- `/predict` writes every call to a PostgreSQL database and returns a `prediction_id` in the response.
2. **Feedback endpoint** -- `POST /feedback/{prediction_id}` accepts operator verdicts and updates the row.
3. **Query endpoints** -- `GET /predictions/recent` and `GET /feedback/stats` for inspecting accumulated data.
4. **Export + retrain pipeline** -- `scripts/export_feedback_dataset.py` snapshots labeled feedback as a fingerprinted dataset, and `ml-model/train_production.py --feedback <file> --promote` combines it with original training data, retrains, evaluates against current production, and only promotes if guardrails pass. This is a real change from the original design (see below).

#### Data model

Single PostgreSQL table `predictions`, served by a dedicated PostgreSQL
StatefulSet in the `ml-serving` namespace (data survives pod restarts and
rolling updates via the StatefulSet's PVC). The API reaches it over
DATABASE_URL; credentials come from a Kubernetes Secret, generated randomly
rather than hardcoded — see [Security posture](#security-posture) for the
real, live password rotation this project performed after finding a
committed password in this exact Secret's original file.

| Column               | Type      | Nullable | Notes                                                        |
|----------------------|-----------|----------|--------------------------------------------------------------|
| id                   | TEXT      | no       | UUIDv4, primary key                                          |
| timestamp            | TEXT      | no       | ISO-8601, when the /predict call happened                    |
| machine              | TEXT      | no       | e.g. web-01                                                  |
| machine_type         | TEXT      | yes      | e.g. web, db, router                                         |
| time_window          | TEXT      | yes      | night / morning / afternoon / evening (named `time_window`: `window` is a PostgreSQL reserved keyword) |
| features_json        | TEXT      | no       | JSON dict of the 6 z-scored feature values                   |
| raw_metrics_json     | TEXT      | no       | JSON dict of the original cpu/ram/network/etc values         |
| history_json         | TEXT      | yes      | JSON dict of the v4 rolling-history window, when supplied (added in the most recent API pass, to make v4 predictions fully reproducible from logged data) |
| model_version        | TEXT      | no       | e.g. telecom_v3 / telecom_v4_rolling                          |
| predict_threshold    | DOUBLE PRECISION | no | e.g. 0.60 (the current, corrected value)                  |
| xgb_p_normal         | DOUBLE PRECISION | yes | Raw probability the model gave for the 'normal' class     |
| xgb_cause            | TEXT      | yes      | Predicted cause (normal / cpu_spike / memory_leak / etc)     |
| iso_score            | DOUBLE PRECISION | yes | IsolationForest anomaly score                             |
| final_is_anomaly     | INTEGER   | no       | 0 or 1, what the API actually returned                       |
| final_cause          | TEXT      | yes      | The cause name returned to the caller (or NULL)              |
| operator_verdict     | TEXT      | yes      | true_positive / false_positive / true_negative / false_negative |
| verdict_timestamp    | TEXT      | yes      | ISO-8601, when the operator submitted feedback               |
| verdict_notes        | TEXT      | yes      | Free-text operator comment                                   |

Verdict semantics:

- `true_positive`: model flagged an anomaly, and it was real
- `false_positive`: model flagged an anomaly, but it was actually fine
- `true_negative`: model said normal, and it really was normal (rarely submitted, since normals are the default assumption)
- `false_negative`: model missed a real anomaly (operator caught it separately, submits feedback referencing the prediction_id of what should have been flagged)

#### API endpoints

**`POST /predict`** -- response includes `prediction_id` (UUIDv4). On every call, writes a new row to the DB with all fields except the three verdict columns (which start NULL).

**`POST /feedback/{prediction_id}`** -- request body `{"verdict": "...", "notes": "optional string"}`. Returns 200 with the updated row, 404 if prediction_id not found, 400 if verdict value is invalid. Idempotent: submitting feedback twice overwrites the previous verdict with the newer timestamp.

**`GET /predictions/recent?limit=N`** -- returns the N most recent predictions from the DB.

**`GET /feedback/stats`** -- returns counts of each verdict type across the whole DB.

All feedback-DB endpoints now return `503` (not an uncaught `500`) when the database is genuinely unreachable — see [Engineering decision 23](#23-why-feedback-logging-is-best-effort-not-fail-loud-graceful-degradation).

#### Storage

A PostgreSQL StatefulSet (`postgres`) in the `ml-serving` namespace, backed by
a persistent volume claim. A headless Service gives the StatefulSet a stable
network identity; a Secret (`postgres-credentials`) holds the credentials,
injected into both PostgreSQL and the API via env vars, generated with a
random password by `scripts/ensure_db_secret.sh` (demo profile) or from an
Ansible Vault value (production profile) — never a committed literal, since
the most recent security pass found and removed one. Chosen over an earlier
SQLite-on-a-PVC approach because:

- **Concurrent multi-writer safety**: multiple API replicas write simultaneously
  without file-lock contention (verified: 20 parallel writes in 0.45s). SQLite
  on a shared file would serialize or corrupt under this.
- **Survives a hard pod kill**: verified by deleting the postgres pod -- the
  StatefulSet recreates it and the PVC preserves all rows. Verified again,
  live, during the most recent security pass, when the Secret itself was
  rotated: the StatefulSet and PVC were deleted deliberately (PostgreSQL only
  applies a password at first initialization, so rotating the Secret alone
  would not have changed the running database's actual password), recreated
  with the new random credentials, and the whole feedback loop — a real
  prediction, a real operator verdict — confirmed working end to end before
  the change was considered done.
- **Standard client-server DB**, the real production pattern, not a
  shared-file compromise.

Feedback logging is best-effort (see [Engineering decision 23](#23-why-feedback-logging-is-best-effort-not-fail-loud-graceful-degradation)): if PostgreSQL
is unreachable, the API keeps serving predictions and simply skips logging
rather than failing the request, and the connection now retries automatically
every 30 seconds.

#### Export + retrain pipeline (updated — real change from the original design)

The original design here used a dedicated `scripts/retrain_from_feedback.py`
script. **That script has been removed and replaced** by
`ml-model/train_production.py`, the single canonical training path used for
every model this project ships — see [Engineering decision 24](#24-why-the-retrain-guardrail-is-strict-and-what-500-rows-proved-updated)
for exactly why and what changed (the guardrail margins in particular:
zero aggregate F1 regression allowed, a 2-point recall margin, stricter than
the original 5-point design). The pipeline today:

1. `scripts/export_feedback_dataset.py` -- queries the feedback DB, filters
   to rows with `operator_verdict IS NOT NULL`, converts each to a
   training-format row (label from the verdict, cause from `final_cause` for
   confirmed true positives), and writes a fingerprinted CSV snapshot.
2. `ml-model/train_production.py --train <original> --feedback <export> --promote`
   -- fingerprints every input, trains baselines + v3 + v4 + IsolationForest
   on the combined data, evaluates the **candidate and the current production
   model with the identical served decision rule** on the same independent
   test set (reported), applies the guardrail (decided, with `--promote`, on the independent
   validation fleet, not on the test set), and only if it passes, writes the new
   artifacts and records full lineage (including the feedback file's own
   fingerprint) in `models/manifest.json`.
3. If the guardrail rejects the candidate, production is left completely
   untouched — verified by test
   (`tests/test_train_production.py::test_rejected_candidate_leaves_production_untouched`)
   to be byte-identical, not just "probably fine."

#### Feedback and the safety net's contamination — measured

A natural question is: *since the operator judges readings, why not add those judged
rows to the dataset and improve the unsupervised IsolationForest too — or recompute its
`contamination`?* Measured answer: feedback retrains the **classifier only**, and the
net's `contamination` deliberately stays tied to the base fleet. Three reasons, with
numbers from the real feedback DB (282 judged rows at the time of writing).

**1. The net is unsupervised — a verdict is a label, and the net ignores labels.** The
only value it takes from the data is `contamination`, the base anomaly rate
(`float(train["label"].mean())` = **0.0675**, which equals the manifest's
`iso.contamination` exactly — the raw CSV and the shipped model agree).

**2. The judged rows are a *flagged* pool, not a representative one.** Operators judge
alerts, and a flagged reading is ~67% likely to be a true anomaly (that fraction *is* the
detector's precision). Folding flagged feedback into `contamination` drags it from 0.0675
toward 0.67, and the net would over-flag. The numbers below are the **verbatim output** of
`scripts/contamination_ab.py`, which reads the real base rate from the training file and
projects the two cases (run it to reproduce them):

```console
$ python3 scripts/contamination_ab.py
Base anomaly rate (true contamination): 0.0675   [measured from data/telecom_fleet_v2_labeled.csv]
Base training fleet size:               17,280,000 rows
Flagged-pool anomaly fraction (Case A): 0.6713   [= detector precision]

 judged rows added |  Case A (flagged only) |  Case B (representative)
------------------------------------------------------------------------
                 0 |                 0.0675 |                   0.0675
             1,000 |                 0.0675 |                   0.0675
            10,000 |                 0.0678 |                   0.0675
            50,000 |                 0.0692 |                   0.0675  <- inflating
           100,000 |                 0.0709 |                   0.0675  <- inflating
           500,000 |                 0.0845 |                   0.0675  <- inflating
         1,000,000 |                 0.1005 |                   0.0675  <- inflating
```

The base rate it measures from the raw CSV (0.0675) equals the manifest's
`iso.contamination` (0.06747) — the data and the shipped model agree.

**3. The real feedback is far too small to matter anyway.** This one is empirical, not
projected: `scripts/contamination_empirical.py` reads the judged rows exported from the
feedback DB, counts the real anomalies and real normals by operator verdict, appends them and
recomputes `contamination`. Verbatim output:

```console
$ python3 scripts/export_feedback_dataset.py     # writes data/feedback/feedback_<ts>.csv
$ python3 scripts/contamination_empirical.py
Judged rows (real):         282   from 1 file(s)
  verdict breakdown:        {'true_negative': 224, 'true_positive': 34, 'false_positive': 15, 'false_negative': 9}
  -> real anomalies:        43   (true_positive + false_negative)
  -> real normals:          239   (false_positive + true_negative)

Empirical anomaly fraction of the judged pool: 0.1525

Base fleet:                 17,280,000 rows at contamination 0.0675
Case A  add judged as-is:   0.067475   (delta +0.000001)
Case B  same count, repr.:  0.067474   (delta +0.000000)
```

That pool is 15% anomalies rather than the ~67% of a pure alert queue because this particular
set also contains 224 judged *normals* (`true_negative`): the control panel's Demo tab judges
unflagged readings too, which makes it closer to representative. Restricted to the **flagged**
rows alone (TP+FP = 49), the anomaly fraction is 34/49 = **0.69** — the ~67% figure, confirmed
on real verdicts.

So the 282 real judged rows change `contamination` by **+0.000001**. To move it you would need ~500k+ judged rows — years of
hand-judging, since a 200-machine fleet produces ~323,000 alerts *per week* and no human
judges that many. The only way a single week *would* move it is auto-judging every alert and
folding it in (Case A), which pushes 0.0675 → ~0.079 and makes the net over-flag — exactly the
bias the design avoids.

**Why feedback is still safe for the classifier:** it learns `P(cause | features)`, so a
correct (features → label) pair is valid regardless of how the row was sampled, and its
threshold is re-selected each run on the representative validation fleet with the guardrail
checking it there. The net has no such correction, so its base rate is kept representative.

**The correct way to grow the net (future work):** a *random sample of unflagged readings as
pseudo-normal* (representative, Case B), not the judged alerts.

> **Independent validation.** The live operator verdicts reproduce the offline metrics:
> among the real flagged judgments, precision = 34/49 = **0.69** (offline 0.6713) and
> recall = 34/43 = **0.79** (offline 0.7979) — from a 282-row sample, so "consistent with",
> not a tight estimate, but the demo behaves like the measured model.

#### Deployment cycle after a promoted retrain

A promoted retrain writes new artifacts directly; getting them served is the
same automated path every other change takes through this project's CI/CD —
see [L3 — CI/CD](#l3--cicd). `Jenkinsfile.model` is the dedicated, on-demand
job for the whole cycle (export → train → guardrail → commit → trigger the
main deployment pipeline if promoted) — committed and syntax-valid, though
not yet run for real end to end; see [Known limitations](#known-limitations-and-future-work)
for what it still needs.

#### Rollback story

If a promoted retrain later turns out to be a real regression the guardrail missed:

1. Docker image tags are immutable — every previous build's image still exists
2. `kubectl set image deployment/anomaly-api api=<previous-tag> -n ml-serving`
3. K8s rolling update reverts, no data loss (the feedback database is untouched by a code/model rollback)
4. Retrain again, or investigate why the guardrail didn't catch the regression

#### Explicitly deferred to future versions

- Operator UI / dashboard -- feedback submitted via the training/operator tabs in the control panel, or directly via the API
- A scheduled (rather than manual/on-demand) retrain trigger
- Multi-model A/B routing (each pod currently runs one model version at a time)
- Feedback pruning / TTL policy (old feedback stays forever, fine for a demo)
- A feedback UI for a real telecom NOC (out of scope, would be an entire separate project)


### Real bugs found and fixed

- **Evaluation did not fit in memory, and it froze the VM.** `evaluate_set` built features for all 17.28M test rows at once (a full DataFrame copy, the 6- and 15-feature matrices, three sets of class probabilities, and the IsolationForest run three times on the same input), for the candidate and then for production. With the platform running alongside it, the VM swapped until it became unresponsive and had to be hard-restarted. Re-run under a hard cap (`systemd-run -p MemoryMax=11G -p MemorySwapMax=0`), the kernel killed only the training process at 11.5 GB resident, which confirmed the cause without taking the VM down. Fixed by evaluating 20 whole machines at a time: rolling features are per machine, so whole-machine chunks produce identical features, and every metric is built from integer counts (TP/FP/FN, hits per cause, correct causes) that sum exactly across chunks. The IsolationForest now runs once per chunk instead of three times, and v4 probabilities are computed once for both thresholds. **Proven exact, not assumed:** re-scoring the production model with the new code rewrote its recorded evaluation with every metric byte-identical (`git diff` changed only the timestamp), and a full train-and-evaluate run then completed under the same 11 GiB cap.
- **Promotion could inherit the previous model's threshold.** `train_production.py` defaulted to the production model's recorded threshold, so a promoted candidate was evaluated, compared by the guardrail, and recorded at a threshold that had been selected for a different model; the promoted manifest also dropped the selection evidence, which the CI gate only checked when present. `Jenkinsfile.model` additionally defaulted `PROMOTE` to true and never selected a threshold. Fixed so the lifecycle is enforced instead of documented: `--promote` selects the candidate's own threshold on the validation fleet (and refuses a missing or non-independent one), the selection record carries the SHA-256 of the artifacts it was computed for, the CI gate requires that record and rejects a mismatch, `PROMOTE` defaults to false, and the model job runs the gate before committing. The chunked selection reproduced the recorded 0.60 selection of the current production model (maximum difference 0.0001 over 19 thresholds and 4 metrics), which is what justified attesting the existing artifacts.

### Files

| File | What it does |
|---|---|
| `ml-model/generate_telecom_fleet.py` | The synthetic-fleet generator. 200 machines across 11 roles, 30 days of readings, injects the six anomaly causes with full ground truth. |
| `ml-model/preprocess.py` | Baseline construction, z-scoring, and rolling-feature computation — imported directly by `api/app.py`, so training and serving compute features identically by construction. |
| `ml-model/preprocess_robust.py` | A second baseline implementation using trimmed/winsorized statistics, used only inside `benchmark.py` as the "robust z-score" comparison row. |
| `ml-model/decision.py` | The served decision rule (`classifier OR IsolationForest`), scalar and vectorized forms, verified to agree exactly. Imported by both the API and every evaluation script. |
| `ml-model/train_production.py` | The single canonical training path — the only route to a promoted production model. |
| `ml-model/select_threshold.py` | Sweeps the decision threshold on the validation fleet, maximizing F2. |
| `ml-model/benchmark.py` | The six-model unsupervised comparison, time-ordered per-machine split, per-machine point-adjustment. |
| `ml-model/root_cause.py` | Dependency-graph root-cause ranking, backs `POST /root-cause`. |
| `ml-model/zscore_demo.py` | A standalone demonstration that the same raw value is scored differently by machine and hour. |
| `ml-model/load_smd.py` | Adapter for the real Server Machine Dataset (28 real machines, anonymized channels) — not part of the production training path. |
| `ml-model/train_serving_telecom.py`, `train_serving_telecom_robust.py` | Earlier, standalone trainers predating `train_production.py`'s unification. |
| `ml-model/train_serving_telecom_novelty.py` | The novelty-detection experiment (tested, rejected, kept as evidence). |
| `scripts/export_feedback_dataset.py` | Snapshots labeled operator feedback as a fingerprinted training dataset. |
| `scripts/verify_model_manifest.py` | The CI model gate — artifact SHA-256 + recorded F1 + deployment-file threshold and `MODEL_NAME` agreement. |
| `scripts/live_k8s_validation.py` | Real-cluster validation against the independent test set. |
| `scripts/master_comparison.py` | The 12-configuration threshold/feedback experiment. |
| `models/telecom_xgb_classifier_v2.pkl`, `telecom_xgb_label_encoder_v2.pkl` | v3 XGBoost classifier + encoder. |
| `models/telecom_xgb_v4_rolling.pkl`, `telecom_xgb_v4_rolling_encoder.pkl` | v4 XGBoost classifier + encoder. |
| `models/telecom_iso_v2.pkl` | The IsolationForest safety net, shared by v3 and v4. |
| `models/telecom_baselines_v2.json` | Per-machine, per-window z-score baselines. |
| `models/dependency_graph.json` | Router -> machine relationships for root-cause ranking. |
| `models/manifest.json` | Full lineage: artifact SHA-256 hashes, dataset fingerprints, the selected threshold and its evidence, the recorded evaluation. |
| `models/history/` | Every promoted model, timestamped — a real evidence trail. |
| `data/telecom_fleet_v2_labeled.csv` (seed 42), `_val.csv` (seed 7), `_test.csv` (seed 123) | Training, threshold-selection, and evaluation data — three separate files, never mixed. |
| `data/telecom_fleet_v2_operator.csv` (seed 2026, 200 machines, 3 days) | The operator-stream fleet the control panel samples from. Judged readings become feedback rows used for retraining, so it is deliberately not the training, validation or test file. |
| smoke-scope fleets (generated in the Jenkins workspace: 30 machines, 3 days, seeds 42 and 123) | A plumbing check for `Jenkinsfile.model` with `SCOPE=smoke`: 5 minutes, no memory risk, production untouched. They are not subsets of the real files, and their scores must never be quoted as performance (production scores 0.7291 on the real test file; a smoke-scope fleet gives a different number that means nothing). `SCOPE=full` uses the real files above. |

---

## L1 — Serving API

FastAPI, `api/app.py`. Serves the hybrid v3/v4 decision, root-cause ranking, the feedback loop, and the mission-control operations console.

### Endpoints

Models are baked into the Docker image, so an image tag identifies exact model versions.

```
GET  /health              service status, feature list, fallback chain, full model lineage
GET  /machines             all known machines with type
POST /predict               anomaly score for one machine reading (v3 or v4, auto-selected)
POST /root-cause            rank a batch of anomalous machines by root-cause likelihood
POST /feedback/{id}         record an operator verdict
GET  /predictions/recent    recent logged predictions
GET  /feedback/stats        verdict counts
GET  /metrics                Prometheus exposition
GET  /ui/*                   operations console -- see Security posture
```

#### POST /predict

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
per-feature `z_scores` (operator-facing explainability — "cpu z=7.4" tells
the ops team exactly which metric is driving the alert), `baseline_used`
(which fallback level fired), `likely_cause`, and `model` (which of
`telecom_v3` / `telecom_v4_rolling` actually served this request).

Supplying `history` (see [Rolling features and gradual-onset detection](#rolling-features-and-gradual-onset-detection-v4))
routes to v4 automatically. As of the most recent API hardening pass,
`history` is validated strictly (exactly `cpu`/`ram`/`load_avg`, exactly 10
finite values each) rather than accepted and silently mis-shaped; malformed
or missing metrics, non-finite values, and out-of-range machine names are
all rejected at the API boundary rather than reaching the model.

#### POST /root-cause

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
`likely_root_cause`, `downstream_effect`, or `isolated`.


### Control panel (mission-control UI)

A single web console served by the API at `/ui` that drives the entire
platform by clicking, not commands — built as the live-demo and operator
interface. It calls the existing `/predict`, `/root-cause`, `/machines`,
and `/feedback` endpoints (it does not alter any serving logic) plus small
read-only support endpoints (`/ui/status`, `/ui/metrics`, `/ui/infra`, `/ui/fleet`).

**Access is now gated.** `/ui/*` returns `404` unless `ENABLE_OPS_UI=1` is
explicitly set — never set on the Kubernetes serving pods — and requires
HTTP Basic auth, compared in constant time, when enabled. This is a real
change from the console's original, unauthenticated design; see
[Security posture](#security-posture).

Tabs:

- **Status** — live health of all platform layers: API, Docker registry,
  Jenkins, SonarQube, MLflow, Kubernetes pods, Prometheus, Grafana, and every
  exporter, each with an up/down indicator, plus live fleet metrics from
  Prometheus. Auto-refreshes every 10s.
- **Operator** — submit a single reading and see the prediction, likely
  cause, model votes, and per-feature z-scores, with quick presets (normal /
  memory-leak / disk / cpu).
- **Root Cause** — inject a router cascade (a router plus its real
  `dependency_graph.json` dependents) and watch `/root-cause` rank the
  culprit vs the downstream victims.
- **Demo** — readings are answered by the cluster API, which logs them, so each result has operator-verdict buttons (or one button that judges them all with the real labels) that feed the retraining; if the cluster is unreachable it falls back to the local API and marks the rows "not logged".  send batches of **real, held-out test readings** and score the
  model live: a table of real-vs-predicted with a TP/TN/FP/FN verdict per
  row, a running precision/recall/accuracy scorebar, and an expandable
  per-prediction detail.
- **Infrastructure** — Docker containers and images, Kubernetes pods (image
  tags, restart counts), `kubectl top`, HPA status, Prometheus target
  health, and the machines currently flagged anomalous.
- **Fleet** — the live topology: every router and its dependents, with an equipment icon, coloured
  by the pods' own verdicts (red anomalous, green normal, dashed when it stopped reporting, greyed out
  if never scored). Each router card is
  classified by `api/fleet_rules.py` (unreachable, router incident, suspected gray failure,
  router only), the rule measured in [Decision 9c](#9c-failure-model-v3--router-incidents-modelled-the-way-networks-actually-fail).
- **Continuous training** — read-only view of the retraining loop: predictions logged by the cluster and how many an operator judged, the model the cluster serves (from its `/health`), the model in the repository manifest, the recent retraining runs, and links to the Jenkins job and MLflow. The retraining itself runs in Jenkins (`Jenkinsfile.model`).
- **Training** — the training console, see [below](#training-console-interactive-mlops-train-and-verify).
- **Review** — the anomalies the pods flagged that nobody has judged yet. *Real* or *False alarm*
  turns a row into training data for the next retraining run. *Details* opens a pop-up with the
  reading's decision, metrics, z-scores, recent history and times, and, for readings replayed from the
  labelled fleet, the real label from the dataset (`api/demo_labels.py`).
- **Project** — a results summary.

#### Honest demo evaluation, and a bug it exposed

The Demo tab is deliberately built to be *honest*, and getting there
surfaced a real, instructive bug. Two design rules make it trustworthy:

1. **It evaluates on genuinely held-out data.** The readings are sampled
   from `data/telecom_fleet_v2_operator.csv`, a fleet generated with its own seed that is disjoint from the training, validation and test sets (judged readings are used for retraining, so they must never come from the test set). It is held-out in the sense that no model was trained on it before the operator feedback. The sampler seeks across the whole file so all machine
   types appear, and supplies each row's 10-reading rolling history so the
   demo exercises the v4 model, with the row's true label and true
   `anomaly_type` as ground truth.

2. **A demo-side bug found and fixed (wrong time context).** The sampler
   originally sent a hardcoded `hour: 14` for every reading. But the model
   uses **per-time-window baselines**, so a reading actually recorded at
   22:00 was being z-scored against the 14:00 baseline — the wrong window.
   On the same real web-server reading, the correct evening baseline gives
   cpu z = +0.05 (normal) while the hardcoded-afternoon baseline gives
   z = −1.65 (pushed toward anomalous); compounded across metrics, this
   falsely flagged a large fraction of genuinely-normal rows (measured: a
   73% false-positive rate on 15 real normal readings). The fix parses the
   real hour from each row's timestamp; the false-positive rate on the same
   rows dropped to 0%. **This was purely a demo-harness bug — it fed the API
   the wrong time context. The model, the baselines, and the production
   `/predict` path were correct throughout; the documented evaluation
   metrics were never affected.** Kept here because it is a clean
   illustration of why per-time-window baselines matter.

#### How it is served

The control panel ships in the Docker image, so `/ui` is available wherever
the API runs — though the Kubernetes serving pods never set `ENABLE_OPS_UI`,
so it is unreachable there by design. For the demo it runs as a systemd
service (`control-panel.service`, via Ansible — see
[L6 — Ansible](#l6--ansible-infrastructure-as-code)) so its status/infra endpoints have
native access to `kubectl`, `docker`, and Prometheus on the VM.


#### Training console (interactive MLOps: train and verify)

The control panel also exposes the **model lifecycle** interactively, through
a Training tab that turns "train a model, check it" into something you can
watch happen.

The flow has **three** stages today — a real change from the original
four-stage design, explained below:

**1. Train.** Pick any dataset from `data/` (the dropdown scans the folder
live), pick a model, set the hyperparameters (`contamination`, `n_estimators`),
and choose a sample size — from a quick subsample up to the full dataset.
Training reuses the production `preprocess.py`, so the per-machine
per-time-window z-scoring is identical to what the served model uses. It runs
on a background thread with a single-job lock (two trainings cannot run at
once, protecting the shared VM from an out-of-memory cascade).

**2. Compare.** Every run this session lands in a comparison table alongside
the current production baseline, so you can see immediately how a fresh
IsolationForest stacks up: its F1, precision, recall, ROC-AUC, the exact
parameters, and how much data it saw.

**3. Verify (the guardrail).** A run must pass the same style of guardrail
principle enforced by `train_production.py` for the real training path
before it would be considered production-ready — the verdict is explicit
(PASS or REJECT) with the reasoning shown, against the same production
sampling and comparison discipline used everywhere else in this project (see
`tests/test_training_console.py::test_sample_is_spread_over_the_whole_file`
and `::test_candidate_compared_with_production_on_same_rows`, which test
exactly this).

**What changed from the original design: registration and deploy-command
generation have been removed.** The original console had a fourth stage —
"Register + deploy" — that saved a verified run's artifact and displayed the
exact `docker build` / `minikube image load` / `kubectl set image` commands a
real rollout would need. This has been removed in the most recent review
pass: the *real*, guardrailed, lineage-recording path to a promoted model is
now exclusively `ml-model/train_production.py --promote` (used directly, or
via `Jenkinsfile.model`), and having a second, separate "register" mechanism
in the console risked producing an artifact that never went through the same
fingerprinting and manifest recording the real path guarantees. The console
is now explicitly a training and comparison sandbox, not a second path to
production.

The backend lives in `api/training.py` (isolated from the serving path) and
is mounted through the endpoints `/ui/datasets`, `/ui/train`,
`/ui/train/status`, `/ui/train/runs`, and `/ui/verify`. A real path-traversal
vulnerability in the dataset selector — the requested filename was not
checked against a whitelist before being joined into a filesystem path — was
found and fixed in the most recent security pass; see
[Security posture](#security-posture).


### Real bugs found and fixed

- **The operations console had no authentication at all.** `/ui/*` (which can start and stop services, launch training and run recovery scripts) was reachable by anyone who could reach the API. It is now absent (404) unless `ENABLE_OPS_UI=1`, which the Kubernetes pods never set, and behind constant-time HTTP Basic auth when enabled; tested for missing, wrong and correct credentials.
- **Path traversal in the training console.** The requested dataset name was joined straight onto a filesystem path, so `../../../etc/passwd` could be read through the API. It is now checked against the list of real datasets first; tested with five traversal forms.
- **Inputs that silently distorted predictions.** `NaN`/`inf` were accepted, and a `history` window of the wrong length computed rolling features on a different scale than v4 was trained on. Both are now rejected at the API boundary.
- **One database failure disabled feedback logging until the next restart.** It now retries every 30 s, and the feedback endpoints return `503` instead of an uncaught `500`.
- **Three bugs found only by running the tests:** a metrics function that was called but never defined (`NameError` on every request), a missing `p_anomaly` field in both prediction paths, and five IsolationForest call sites passing a DataFrame to a model fitted on arrays (caught by re-running the suite with warnings as errors).
- **The API never logged the rolling history, so operator feedback could not reach v4.** `feedback_db.insert_prediction` accepts and stores `history`, but the two calls in `api/app.py` never passed it: `history_json` stayed NULL on every row, and the model-job smoke run reported `rows_used_v4: 0` although 132 judged rows were used by v3. Fixed by passing the request's history to the logger; covered by two tests (a v4 request logs its history, a request without one logs none).
- **The panel's Stop buttons did nothing for the host layers, and Fix started a second copy.** Prometheus, the Kubernetes exporter and the production agent are systemd units with `Restart=always`. Stop ran `pkill`, which systemd undid seconds later while the card kept saying it had succeeded; Fix spawned the program itself outside systemd (Prometheus even with a different storage path, a second agent would have doubled the traffic into the database). Both now go through `systemctl`, and 10 tests pin it, including that the panel never calls `pkill` or `Popen` for these layers.\n- **A missing production model file failed with an unclear error.** Removing the v4 model already stopped startup, but only as a bare `FileNotFoundError`; it now raises `RuntimeError: production artifact missing: <file>`. Proven by removing the v4 file and importing the app: v4 cannot silently degrade to v3-only.

### Files

| File | What it does |
|---|---|
| `api/app.py` | The FastAPI application — every endpoint above, input validation, Prometheus metrics, artifact-integrity checking before unpickling, and the ops-UI guard. |
| `api/feedback_db.py` | PostgreSQL access for logging predictions and recording operator verdicts. |
| `api/fleet_rules.py` | The Fleet tab's group rule (pure logic, no I/O), shared by `/ui/fleet` and the offline scripts that measure it. |
| `api/demo_labels.py` | Keeps the true label of replayed readings beside the cluster's prediction id, for the Review tab's real-label column. |
| `api/training.py` | Backend for the training console — dataset selection (with the path-traversal fix), representative sampling, comparing a candidate against production. |
| `api/static/control_panel.html` | The mission-control UI, served at `/ui`. |
| `tests/test_api.py` (38 tests), `test_decision.py`, `test_training_console.py`, `test_ui_cluster.py`, `test_ui_fleet.py`, `test_review_details.py` | Covered in full in [Testing](#testing) below. |

---

## L2 — Container Image

### What we built

- Base: `python:3.12-slim-trixie` (upgraded from `3.10-slim`), non-root `appuser` (uid/gid 10001), layer-cached deps, model and dependency graph baked into the image.
- `pip` and `ensurepip` are removed from the final image after dependencies install — neither is needed at runtime, and their presence would be an unnecessary attack surface.
- `HEALTHCHECK` on `/health`, same endpoint K8s liveness probes use.
- Local registry (`registry:2`, port 5000) for Jenkins to push to.
- OS security updates on every build: `apt-get upgrade` runs after the pip layer, keyed on the `APT_REFRESH` build argument (see the bug below).

```bash
docker build --build-arg APT_REFRESH=$(date +%s) -t devsecmlops-api:$(cat VERSION) .
docker run -p 8000:8000 devsecmlops-api:$(cat VERSION)
```

### Real bugs found and fixed

- **A version pin in a `RUN` command, `wheel>=0.46.2`, was unquoted** — the shell interpreted `>` as a file redirect, so `wheel` was actually installed completely unpinned, silently, into a file literally named `=0.46.2`.
- **The OS security upgrade was cached forever.** The Dockerfile ran `apt-get upgrade` in an early layer whose comment claimed it would "only invalidate when Debian ships new patches", which is not how Docker's cache works. The layer is reused as long as the instruction is unchanged. Found when Trivy blocked build `b91` on 6 HIGH OpenSSL CVEs that Debian had already fixed (`3.5.7-1~deb13u3`): the upgrade layer had been cached since 2026-09-29. Fixed by moving the upgrade after the pip install and keying it on a build argument that Jenkins sets to the unique build tag, so every build re-runs it without invalidating the Python dependency layer; `--pull` also refreshes the base image. Verified by reading the OpenSSL package version from a locally built image: `3.5.7-1~deb13u3`.
- **The comment `# Copy the ONE shipped model artifact` preceded copying 6+ files** (v3/v4 XGBoost, encoders, IsolationForest, baselines, dependency graph, manifest) — misleading for anyone reading the Dockerfile to reconstruct what's actually there. Corrected to describe what's real: `# Copy production inference artifacts (v3/v4 XGBoost + IsolationForest safety net)`.
- **The legacy `MODEL_NAME=telecom_v2` path in `docker-entrypoint.sh` defaulted to `MINIO_ACCESS_KEY=admin` / `MINIO_SECRET_KEY=minioadmin123` when unset**, substituted directly into a real boto3 client — silently connecting with publicly-known credentials rather than failing. Fixed to fail closed (`: "${VAR:?message}"`), consistent with this project's security posture everywhere else. Verified live: unsetting both vars now genuinely aborts with a clear error instead of silently proceeding.

### Files

| File | What it does |
|---|---|
| `Dockerfile` | The image build — Python 3.12, non-root, hardened, model artifacts baked in. |
| `docker-entrypoint.sh` | Startup script; only does real work for the legacy `telecom_v2` path (MinIO fetch), fails closed if MinIO credentials aren't supplied. |
| `.dockerignore` | Excludes `venv/`, `data/`, old models from the build context. |
| `requirements-api.txt` | Pinned, curated dependencies — the same versions the host venv uses, since pickled models must be loaded by the version that wrote them. `boto3` (used only by the legacy v2 MinIO path) and `msgpack` (a security floor) are pinned in the Dockerfile; their transitive dependencies are bounded by those pins, not locked. |

---

## L3 — CI/CD

Jenkins, `Jenkinsfile`, 11 stages, every one a real gate — a failure stops the pipeline before anything downstream is built, pushed, or deployed. The pipeline used to have 9-10 stages with two structural weaknesses: the SonarQube Quality Gate could not actually stop the build (`abortPipeline: false`), and Trivy's scan result was discarded (`|| echo "... non-blocking"`). Both are fixed.

### What we built

Stage labels below match the Jenkins console exactly.

- **1. Checkout**
- **1b. Unit tests** — pytest, 179 tests, coverage report. Fail-fast: a broken commit stops here.
- **2. SAST** — SonarQube.
- **2b. Quality Gate** — `abortPipeline: true`.
- **3. Repository scan + model gate** — `trivy fs` on the repository itself (dependencies, secrets, IaC misconfigurations), and `scripts/verify_model_manifest.py --min-f1 0.60`, checking artifact integrity, the recorded F1, that no deployment file carries a copy of the threshold (it is read from the manifest) and that the manifest's threshold is the evaluated one, and that this threshold was selected on an independent validation fleet for exactly these artifacts — *before* an image is built.
- **4. Build Docker image** — `docker build --pull --build-arg APT_REFRESH=<build tag>`: refreshes the base image and re-runs the OS security upgrade on every build, while the Python dependency layer stays cached.
- **5. Container smoke test** — the image runs with the **same runtime restrictions the real pod uses** (`--read-only`, `--tmpfs /tmp`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--user 10001:10001`) on `devsecmlops-net`, checked by `scripts/smoke_test.py` (health, version, a v3 prediction, a v4 prediction, metrics, ops UI absent).
- **6. Image scan + SBOM** — `trivy image --exit-code 1`, plus a CycloneDX SBOM archived as a build artifact.
- **7. Push to registry** — followed by a registry-catalog check over `registry:5000`.
- **8. Deploy to Kubernetes** — `docker save | docker exec -i minikube docker load`, `kubectl set image`, `kubectl rollout status`.
- **9. Post-deploy smoke test** — the same `scripts/smoke_test.py`, piped into the live pod via `kubectl exec`.

`Jenkinsfile.model` is the separate, on-demand job for the model-retraining cycle — see [L0's feedback loop](#feedback-loop-and-retraining).

### What we tested — every real run, in order

Each failure below was a genuine problem, diagnosed from the log and fixed before the next run:

| Run | Result | Cause | Fix |
|---|---|---|---|
| 1 | Stopped at 2b | Quality Gate `ERROR` — undocumented `503` responses, style findings | Documented the responses, fixed the findings |
| 2 | Stopped at 2b | Quality Gate `ERROR` — two more `503`s, three findings confirmed as false positives | Fixed; false positives marked in SonarQube with reasoning |
| 3 | Failed at 3 | Trivy's 117MB vulnerability DB download timed out | `--timeout 15m` on every Trivy call |
| 4 | **SUCCESS** | — | All stages passed, zero image vulnerabilities |
| 5 | Failed at 2 | `sonarqube: Name or service not known` | Declared `devsecmlops-net` on registry, minio, sonarqube in Ansible |
| 6 | Failed at 2b | SonarQube task `FAILED` — Elasticsearch read-only at 95% disk | Reclaimed ~11GB of unused images and build cache |
| 7 | **SUCCESS** | — | Build `2.20.3-b87`: all stages passed, zero vulnerabilities, model gate F1 0.7246, post-deploy smoke test through the live pod |
| 8 | **SUCCESS** | — | Build `2.20.3-b88`: first run with the `MODEL_NAME` check in the model gate; then used to prove Ansible no longer rolls back Jenkins releases (see L6) |
| 9 | Stopped at 2b | Quality Gate `ERROR`: `"v4_at_0.5"` duplicated 3 times in the new chunked evaluation (S1192) | Named constant `V4_AT_05`; `;`-joined statements split |
| 10 | Failed at 6 | 6 HIGH OpenSSL CVEs (fixed in Debian `3.5.7-1~deb13u3`): the `apt-get upgrade` layer had been cached since 2026-09-29 | Upgrade moved after pip and re-run on every build (`APT_REFRESH`), plus `--pull` (see L2) |
| 11 | **SUCCESS** | — | Build `2.20.3-b92`: OpenSSL at `deb13u3`, zero image vulnerabilities, all stages passed, deployed and smoke-tested in the live pod |
| 12 | **SUCCESS** | — | Build `2.20.3-b93`: first CI run of the model gate that requires the threshold selection to match the promoted artifacts, Sonar analyzing as Python 3.12, `boto3`/`msgpack` pinned; 65 tests |
| 13 | **SUCCESS** | — | Build `2.20.3-b94`, the first green run on the final code: 66 tests, Sonar Python 3.12 gate OK on the new selection-tool code, model gate with the structured threshold attestation, 0 image vulnerabilities, smoke-tested in the live pod |
| 14 | **SUCCESS** | — | Build `2.20.3-b95` on the final commit: 67 tests, Sonar gate OK on the enforced `--record` rule, model gate OK, 0 image vulnerabilities, smoke-tested in the live pod |
| 15 | Stopped at 2b | Quality Gate `ERROR` on the continuous-training panel code (build `b96`): a hardcoded-IP review item, four undocumented HTTP error codes on the cluster routes, a `for` loop in the panel JS | Error codes documented in `responses=`, `for-of`; the IP (Minikube's fixed node address) reviewed in SonarQube |
| 16 | **SUCCESS** | — | Build `2.20.3-b97`: 86 tests, Sonar gate OK on the panel, sampler and history-logging code, 0 image vulnerabilities, deployed and smoke-tested; the first image whose API logs the rolling history with every prediction |
| 17 | Stopped at 2b | Quality Gate `ERROR` on build `b108` (commit `89c124ec`): 15 new maintainability issues in the Fleet/Review panel code and the v3 generator (nested ternaries, cognitive complexity 22/37/20, a duplicated Prometheus URL, a `# noqa` comment format) | Refactored without changing behaviour: generator output byte-identical, page functions give identical HTML on 88 cases |
| 18 | **SUCCESS** | — | Build `2.20.3-b109` (commit `20113d7b`): 179 tests, Sonar gate OK, repository scan clean, model gate F1 0.7291, 0 image vulnerabilities, deployed and smoke-tested in the live pod |
| 19 | **SUCCESS** | — | Build `2.20.3-b111` (commit `bd0fdd67`), started by model build 9 after it promoted the retrained model: 179 tests, Sonar gate OK, model gate F1 0.7292, 0 image vulnerabilities, deployed and smoke-tested in the live pod |

Builds b98 to b107 and b110 are not listed one by one. Run 17 is the same gate stopping new code: the operations UI
and the v3 generator, both covered by tests, still had to meet the zero-new-issues rule.

Runs 1 and 2 are the evidence that the gate genuinely blocks: every later stage was skipped, nothing was built, pushed, or deployed.

**Earlier-cycle fixes, kept as real history:** 2 HIGH CVEs (CVE-2026-24049 wheel, CVE-2026-23949 jaraco.context) found by Trivy and fixed by version pinning; a SonarQube cycle that flagged a **hardcoded PostgreSQL password (Blocker)** and a cognitive-complexity violation on `predict()`, both fixed; a smoke-test robustness fix so the container serves `/health` even with no database reachable.

### Real bugs found and fixed

- **The feedback exporter crashed on the first non-empty export.** `export_feedback_dataset.py` printed `out.relative_to(ROOT)`, which raises for a relative `--out-dir` (as the model job passes it). It stayed invisible while the table was empty, because an empty table returned before that line; the first Jenkins run with judged rows hit it. Fixed and covered by four tests with a stubbed `kubectl`, two of which fail on the old code.
- **The container smoke test curled `localhost` from inside the Jenkins container**, which can never reach a separately-networked test container — it always silently passed via a swallowed shell error. Fixed: the test container now joins `devsecmlops-net` and is addressed by name.
- **SonarQube became genuinely unreachable from Jenkins mid-session**: `sonarqube: Name or service not known`. Root cause, confirmed live: `registry`, `minio`, and `sonarqube` were never declared on `devsecmlops-net` in their Ansible task definitions at all — each one silently defaults to Docker's plain bridge network. They had likely been attached manually at some earlier point, outside Ansible's own knowledge, which worked until an Ansible run recreated all three containers (image/config changes), silently dropping the undeclared attachment with no warning until the next Jenkins build. Fixed by adding an explicit `networks:` list to all three tasks — see [L6](#l6--ansible-infrastructure-as-code) for the full fix and live verification.
- **SonarQube's own background analysis then failed with task status `FAILED`** (distinct from a normal `ERROR` quality-gate condition — `FAILED` means SonarQube's server-side processing crashed). Confirmed via its own logs: `flood stage disk watermark [95%] exceeded ... all indices on this node will be marked read-only` — the VM's disk was at 95% used, 3.9GB free, and Elasticsearch's own safety mechanism locked every index read-only, so SonarQube could not write the analysis results. Fixed by reclaiming ~11GB via `docker image prune` (unused image tags — every Jenkins build creates a new one, and old ones never got cleaned) and `docker builder prune` (stale build cache), bringing disk usage back under the watermark.

### Files

| File | What it does |
|---|---|
| `Jenkinsfile` | The 11-stage application pipeline. |
| `Jenkinsfile.model` | The on-demand model-retraining pipeline. |
| `jenkins/Dockerfile` | The custom Jenkins image (python3, kubectl, minikube, sonar-scanner, trivy, docker CLI). |
| `scripts/smoke_test.py` | Standard-library-only health/version/v3/v4/metrics/ops-UI check, used before and after deployment. |

---

## L4 — Kubernetes

### What we built and tested

Minikube, single-node, Docker driver. Namespace `ml-serving`.

- **`anomaly-api` Deployment** — hardened as in
  [Security posture](#security-posture); the DB
  credentials come from the Secret, never hardcoded, and the alert threshold is not set here at
  all: the API reads it from `models/manifest.json`.
- **`postgres` StatefulSet** — hardened the same way, running as `uid 70`
  (PostgreSQL's own official convention). Its Secret is generated with a
  random password rather than the committed literal this project shipped
  with earlier.
- **HPA** — 2–5 replicas, target 70% CPU. **Confirmed live, in both
  directions**: under real, sustained load from the production traffic
  agent, the deployment scaled from 2 to 5 replicas; after a VM restart
  cleared that load history, it correctly settled back to 2 rather than
  staying inflated — proof the autoscaler works both up and down, not just
  up.
- **NodePort** `:30080` — used by `scripts/live_k8s_validation.py` and any
  external client. Metrics-server is enabled for real HPA readings.
- **Automated deployment from Jenkins.** Jenkins and Minikube are separate,
  sibling Docker containers — Jenkins has no built-in path to the cluster.
  `minikube image load`'s Docker driver uses SSH internally via a
  host-loopback address that only resolves correctly from the real host,
  never from inside any container — confirmed directly (a `dial tcp
  127.0.0.1:<port>: connect: connection refused` from inside Jenkins,
  matching kubernetes/minikube#16293 and #15260 for nested-container
  Minikube access). Routed around entirely: `docker save <image> | docker
  exec -i minikube docker load` pipes the image through the Docker socket
  already shared between Jenkins and the cluster — the same mechanism every
  other `docker` command in the pipeline already uses. Jenkins carries its
  own copy of `kubectl`, the `minikube` CLI, a working kubeconfig, and the
  full Minikube certificate directory (installed into the container's
  persistent volume so it survives a Jenkins restart), plus explicit
  `KUBECONFIG`/`MINIKUBE_HOME` environment in the deploy stage.

**Operational incidents, documented — real, from this project's own
development, several predating and one confirmed again during the most
recent pass:**

- **Docker-daemon restart leaves Minikube's networking stale.** The cluster
  container stays "Up" but is unreachable — diagnosed via a drifting
  host-port mapping. Standing rule: always `minikube start` (never `docker
  start minikube`) after any Docker daemon restart, since only `minikube
  start` re-establishes networking and certificates.
- **Swap exhaustion masquerading as a Kubernetes hang.** `minikube start`
  timed out during container creation with CPU load near zero — not
  resource contention. Root cause: system swap was ~97% full, and any
  memory-allocating process stalled on swap I/O despite real RAM being
  free. `swapoff -a && swapon -a` resolved it immediately with zero
  disruption to running processes.
- **Minikube's own image cache is separate from Docker's.** Even with the
  base image already present in `docker images`, `minikube start`
  re-downloaded it on every fresh cluster creation, because `minikube
  delete` clears a separate cache directory Minikube consults first. Fixed
  by pre-populating that cache directly; confirmed to survive repeated
  `minikube delete --all --purge` cycles.
- **Docker network conflicts on `192.168.49.2`, from more than one
  source.** Minikube always wants this fixed address for its own node. At
  different points, three different, unrelated containers ended up holding
  it. A recovery step that disconnects one hardcoded container name is
  blind to the others; the fix looks up whichever container *currently*
  holds the address and evicts it by IP, not by name. This exact class of
  problem was hit again, live, during the most recent infrastructure pass
  (a Jenkins container recreation during an Ansible run coincided with a
  Minikube networking error) — confirmed on inspection that a different,
  unrelated container held the address at `.3`, not `.2`, so on that
  occasion the real cause was a transient clash during simultaneous
  container recreation, not a squatter; `scripts/k8s_recover.sh` (the
  productionized version of this same fix) resolved it.
- **Cluster unreachable after a reboot, stop or pause (root-caused, fixed
  permanently).** Three faults were stacked. (1) A boot-order race over the
  fixed address: Docker restarts the Jenkins container (restart policy)
  before anything starts Minikube, and Jenkins, attached to the same
  network, could take `192.168.49.2`, so the cluster container failed with
  "address already in use". (2) Nothing started the cluster: the `minikube`
  container has restart policy `no`. (3) `minikube start --driver=docker`
  as root is refused without `--force`, so the gentle start failed and the
  recovery script escalated to a destructive rebuild. Fix: Jenkins is
  pinned to `192.168.49.10` (re-applied by `scripts/k8s_recover.sh`, which
  also never evicts the `minikube` container and now evicts a squatter
  before the gentle start), and `scripts/systemd/minikube-start.service`
  starts the cluster after Docker. Verified by a VM reboot: the cluster
  returned by itself with no recovery script, the same PostgreSQL pod and
  volume came back, and all data survived (60,699 predictions, 180 operator
  verdicts). The pause/resume scenario is not covered by a test. Details and
  verification commands: `docs/MINIKUBE_RELIABILITY.md`.
- **A real, live-tested password rotation.** PostgreSQL only applies a
  password at first initialization — rotating the Secret alone would not
  change a running database's actual password. The full rotation this
  project performed deleted the StatefulSet and its PVC deliberately,
  regenerated the Secret with a random password, redeployed fresh, and
  confirmed the whole feedback loop (a real prediction, a real operator
  verdict) working with the new credentials before considering it done.

```bash
minikube start --driver=docker --force
minikube addons enable metrics-server
kubectl apply -f kubernetes/namespace.yaml
kubectl apply -f kubernetes/postgres.yaml
kubectl apply -f kubernetes/deployment.yaml
kubectl apply -f kubernetes/service.yaml
kubectl apply -f kubernetes/hpa.yaml
kubectl get pods -n ml-serving
```

### Real bugs found and fixed

- **The Ansible Kubernetes template had drifted far behind the real manifest.** `kubernetes/deployment.yaml` was hardened (uid/gid 10001, seccomp `RuntimeDefault`, read-only root filesystem, all capabilities dropped, no service-account token, DB user/password/database all from the Secret). The Ansible template — which is what Ansible *actually* deploys, via `ansible.builtin.template` — still had only `runAsUser: 999`, none of the rest, and a hardcoded `postgresql://feedback:...@.../feedback` connection string. A production-profile Ansible deploy would have shipped an unhardened pod that could not authenticate against a production Secret with a different username. Fixed by syncing the template line-for-line with the real manifest, keeping only the legitimate templating (`api_replicas`, `api_image`, resource-limit toggles). Verified with `--syntax-check` on the production profile and the full test suite, then deployed through the real pipeline.
- **A cluster rebuild resets the deployed image to the git-pinned baseline.** Found live: after Jenkins build `2.20.3-b87`, the Deployment was later running `devsecmlops-api:2.20.3` with rollout history at revision 1, meaning the Deployment had been recreated (a full Minikube rebuild), not updated. A fresh cluster has no record of the last release, so `scripts/k8s_recover.sh` restores the tag pinned in `kubernetes/deployment.yaml`. This is intended: recovery restores a reproducible baseline from git, and the next Jenkins run redeploys the latest release. Ansible rebuilds the baseline image only if it is missing, so after code changes, rebuild it or run Jenkins before a demo. The rebuild also creates a new, empty PostgreSQL volume, so every logged prediction and operator verdict is lost (observed after a VM restart: 57,592 predictions and 48 judged rows before, 14,642 and none after); record judged rows only once the cluster state is final. Since the reboot fix, `scripts/k8s_recover.sh` also dumps the judged rows to `feedback_backup.tsv` before deleting the cluster (or keeps the last saved copy if the database is unreadable) and restores them afterwards; unjudged predictions are still lost.
- **A committed database password** (`feedback-dev-password`, in the now-deleted `kubernetes/postgres-secret.yaml`) — removed, replaced by `scripts/ensure_db_secret.sh`, and rotated live in the running cluster (see the password-rotation incident above).
- **`scripts/k8s_recover.sh` made the Minikube home directory world-readable and world-writable** (`chmod -R a+rwX`) — that directory holds the cluster CA's private key and admin credentials, so any local user could have become cluster-admin. Replaced with a POSIX ACL granting access to exactly one uid.

### Files

| File | What it does |
|---|---|
| `kubernetes/namespace.yaml` | Creates `ml-serving`. |
| `kubernetes/deployment.yaml` | The hardened `anomaly-api` Deployment. |
| `kubernetes/postgres.yaml` | The hardened `postgres` StatefulSet (uid 70), probes using the Secret's own user/database. |
| `kubernetes/service.yaml` | NodePort `:30080`. |
| `kubernetes/hpa.yaml` | HorizontalPodAutoscaler, 2–5 replicas, 70% CPU. |
| `kubernetes/networkpolicy.yaml` | Opt-in: only the API pods may reach PostgreSQL. Not applied on the demo cluster (see Known limitations). |
| `scripts/ensure_db_secret.sh` | Generates a random DB password idempotently (demo profile). |
| `scripts/k8s_recover.sh` | Recovers Minikube and Jenkins's cluster access after a Docker daemon or VM restart. |

---

## L5 — Observability and MLOps

Prometheus + Grafana (25 panels in 5 rows) + realistic traffic replayed from the independent test set + Kubernetes
object monitoring.

### Monitoring

#### Per-pod scraping, not the NodePort (real fix)

Prometheus scrapes every serving pod **individually**, discovered through
the Kubernetes API server — not the NodePort. Scraping the NodePort was
tried and found to mix replicas' data: confirmed live, with two pods behind
it, one had zero recorded metric series, because a load balancer routes
each scrape to a random pod, making a `rate()` query meaningless across
scrapes. Because multiple replicas each only know the requests *they*
served, every per-machine panel resolves the **freshest replica** for that
machine (`api_machine_last_seen_timestamp_seconds`), rather than an
arbitrary one.

#### `production_agent.py` — the current default traffic source

**One independent thread per machine** (not a single shared loop), each on
its own cycle with jitter — in a real fleet, every machine's monitoring
agent runs on its own schedule. Each replays that machine's own real,
**consecutive** readings from the independent test set, keeping the last 10
`cpu`/`ram`/`load_avg` values; once that window fills, requests carry the
machine's genuine preceding readings, so v4 actually serves. A separate
background thread relays currently-anomalous machines to `/root-cause`,
reading Prometheus (never computing anything itself), with the same
freshest-replica resolution as the dashboard.

**A real bug found and fixed in the most recent pass:** an earlier version
picked a **random row per tick** rather than consecutive ones — which meant
it could never assemble a genuine 10-reading history window, so v4 never
actually served through this path at all, silently. Fixed to replay
consecutive segments per machine.

**A second real bug, same class, found and fixed earlier:** the initial
sampling approach seeked to evenly-spaced byte offsets to sample the file
without reading it in full. This can silently alias against periodic
structure in how the file was written — confirmed directly with a
reproduction file where evenly-spaced seeks landed on only 2 of 20 machines
repeatedly (the same effect as a strobe light synced to a rotating wheel).
Fixed with genuinely random seek positions. The same exact bug, in a second
location (the API's own history-sampling endpoint for the demo tab), was
found and fixed again in the most recent pass.

**A third bug, API-contract drift, found and fixed earlier:** the
monitoring relay originally read a metric field the API had since renamed
during the v3/v4 evolution — silently reported zero detections even though
the model was working perfectly. This class of bug (two components evolving
on different schedules) is exactly why live, end-to-end integration testing
matters beyond dashboard inspection.

Two earlier, simpler traffic-generation designs — `replay_exporter.py`
(replays the raw dataset without the per-time-window-aware bridging) and
`anomaly_bridge.py` (an earlier bridging design, superseded by the API's own
self-instrumentation) — remain in the repository, fully functional, but are
not started by default in favor of `production_agent.py`.

#### Kubernetes monitoring

`monitoring/k8s_exporter.py` publishes pod readiness, restart counts,
deployment replica counts, and HPA CPU utilization as Prometheus metrics.

#### Grafana dashboard (25 panels in 5 rows)

Provisioned from the repository (`monitoring/grafana/`) — a fixed
datasource UID plus the dashboard JSON, mounted read-only — rather than
edited live through Grafana's own UI, which is exactly how an earlier
version of this dashboard went stale (it queried metrics from a retired
design months after that design was removed). **Every panel carries a
written description** with concrete normal-vs-concerning guidance,
including, for the disk-space panel, a reference to a real incident this
exact VM hit during this project's development (SonarQube's Elasticsearch
went read-only and failed a Quality Gate when disk space ran out). A real
display bug — a bar-gauge panel run as a range query instead of an instant
one, causing `topk(25)` to return every machine ever in the top 25 across
the whole time window instead of exactly 25 — was found (from the rendered
screenshot, not just the query) and fixed in the most recent pass.

#### Machine roster

`docs/machine_roster.txt`: all 200 simulated machines, name/type/role — 40
web, 35 app, 30 db, 20 cache, 20 queue, 15 batch, 15 edge, 8 router, 7
firewall, 5 dns, 5 voip.

### MLflow and MinIO (experiment tracking, artifact store)

MLflow (experiment tracking and artifact store; the pipeline does not use the Model Registry: a model version is identified by its manifest hashes and the MLflow run ID) backed by MinIO (self-hosted
S3-compatible artifact storage) — self-hosted rather than a cloud SaaS,
satisfying the data-sovereignty constraint from the original project
requirements.

**Why MLflow/MinIO run as standalone services, not inside the Kubernetes
cluster:** same reasoning as Jenkins, SonarQube, and the registry — these
are shared platform/tooling services supporting the development process,
not the product being served to end users.

**Now a real, supervised systemd service (real change from the original
design).** MLflow previously ran as a manually-started process and, at one
point during this project's own development, was not running at all — a
real gap. It is now a systemd unit (`Restart=always`, enabled at boot),
installed by Ansible, with `--serve-artifacts` proxying uploads to MinIO —
clients (the training pipeline, the model Jenkins job) never need MinIO
credentials directly, only the MLflow service does. **Verified with a real
round trip**: a metric and a file artifact logged through the API, then
read back through MLflow from MinIO, byte-for-byte correct.

**The canonical pipeline logs to it (verified, and a gap found on the way).** `ml-model/train_production.py` logs every run (parameters, v3/v4 metrics, git commit, train/test dataset hashes, and the model artifacts) to experiment `telecom-production-model`, but only when `MLFLOW_TRACKING_URI` is set; otherwise it skips silently. For a time MLflow therefore held only experiment-phase runs and nothing from the pipeline that governs production. Closed with a real run (`1cc2b5a0fa75465494301dedeab075a6`): trained, evaluated against production on the independent test set, guardrail `PASS`, logged to MLflow with its artifacts stored in MinIO through MLflow's artifact proxy, and its run id recorded in `models/manifest.json`. `Jenkinsfile.model` sets the variable, so every retraining job it runs is tracked.

**The same pipeline, run by Jenkins at full scale (verified).** `Jenkinsfile.model` with `SCOPE=full`, feedback on and `PROMOTE` off: the 3.17M-row training set and the 17.28M-row test set, 11.5 GiB of free memory at the start, 19 minutes end to end. Candidate v4 F1 0.7280 (precision 0.672, recall 0.794) against production 0.7246, and v3 0.6552 against 0.6496; guardrail `PASS`, deliberately not promoted. The run is MLflow `57e33ba05b444d52aa1bbc0173881d0e` (experiment `telecom-production-model`), and stage 6 read its six model files back through MLflow from MinIO. Feedback: 34 judged rows exported from PostgreSQL through `kubectl exec`, 29 used (by v3 and v4 alike, because every row carries its history) and 5 skipped (3 missed anomalies, which carry no cause, and 2 detections the classifier could not name). The verdicts are simulated: the ground truth of a generated fleet that is disjoint from the training, validation and test sets stands in for an operator. The +0.0002 F1 over the earlier host run (0.7278) is within noise and confounded by the Python version (3.13 here, 3.10 there) and the feedback rows, so no improvement is claimed: 29 rows are 0.001% of the training data.

**Provenance finding from that run (before the 13 October promotion).** Until then the production artifacts were not trained by `train_production.py`. They are the original July models (see `models/history/`), *adopted* into the manifest on 2026-09-25 with their SHA-256 hashes, their hyperparameters read back from the pickles (the manifest records `git_commit: null` for them), and an evaluation on the independent test set. The pipeline's retraining with the same data and hyperparameters scored slightly higher (v4 F1 0.7278 vs 0.7246, v3 0.6551 vs 0.6496). The difference comes from reimplemented training code, not from the evaluation, which reproduces production's recorded numbers exactly. The candidate was promoted on 13 October, after the pipeline re-selected the threshold on the validation fleet (0.60 again). See Known limitations.

**Promotion by Jenkins (verified, 13 October 2026).** `devsecmlops-model` build 7, `SCOPE=full`, feedback on, `PROMOTE` on. Stage 2 exported 262 judged rows from PostgreSQL; stage 4 used 253 for v3 and 214 for v4 and skipped 9 whose cause is unknown. Candidate v4 F1 0.7278 (precision 0.6717, recall 0.7941) against production 0.7246, v3 0.6550 against 0.6496. The threshold was selected on the 8,064,000-row validation fleet and came out at 0.6 (F2 0.7686), not inherited from production. Guardrail `PASS`; MLflow run `f622a7a9b1554c97ad421a92f51f081a`; stage 6 read the six model files back through MLflow from MinIO; stage 7 ran the CI model gate on the promoted manifest (all OK) and pushed commit `18042f37` to `main` with the `github-pat-devsecmlops` token. Stage 8 started the application pipeline: build #100 failed and was re-run by hand as #101, which went green (99 tests, Quality Gate OK, deployed as `devsecmlops-api:2.20.3-b101`). The same 0.7278 appeared on 30 September with no feedback rows, so the gain over the July model is not attributed to feedback (214 rows are 0.007% of the training set), and no improvement is claimed.

**Promotion by Jenkins at full data (verified, 19 October 2026).** `devsecmlops-model` build 8, `SCOPE=full`, feedback on, `PROMOTE` on, with the policy in `config/model_policy.json` (all normal rows, recall target 0.80). The classifier trained on all 17,280,000 rows (1,165,947 anomalies + 16,114,053 normal) in 1,080 s; stage 2 exported 262 judged rows, 253 used for v3 and 214 for v4, 9 skipped. The threshold was selected on the 8,064,000-row validation fleet at 0.91 by the rule `min_recall` (recall 0.8008, precision 0.6712, F1 0.7303), not inherited from production. On the test fleet the candidate scored v4 F1 0.7291 (precision 0.6713, recall 0.7979) against production 0.7278, and v3 0.6664 against 0.6550. Guardrail `PASS`; MLflow run `241447d01be24253a92874716dbfdf18`; stage 6 read the six model files back from MinIO; stage 7 ran the CI model gate (all OK, including "threshold 0.91 read from the manifest, no deployment file carries a copy") and pushed commit `ac581064`; stage 8 started the application pipeline, whose build #103 went green on its own and deployed `devsecmlops-api:2.20.3-b103`. Before that run, the live Deployment still carried an old `PREDICT_THRESHOLD=0.6` that overrode the manifest, because stage 8 only swaps the image; it was removed by hand and the pipeline now removes it on every deploy. After the deploy, `/health` reported `threshold 0.91`, source `models/manifest.json production.decision_threshold`. The F1 gain (+0.0013) is within noise and is not claimed.

**Promotion with feedback, end to end (verified, 21 October 2026).** `devsecmlops-model` build 9, `SCOPE=full`, feedback on, `PROMOTE` on. Stage 2 exported 315 judged rows from PostgreSQL; stage 4 used 304 for v3 and 222 for v4 and skipped 11 whose cause is unknown; the classifier trained on all 17,280,000 rows in 1,134 s (IsolationForest contamination 0.06747). The threshold was re-selected on the 8,064,000-row validation fleet: 0.91 by the rule `min_recall` (recall 0.801, precision 0.6713, F1 0.7304). On the test fleet the candidate scored v4 F1 0.7292 (precision 0.6713, recall 0.7979) against production 0.7291, and v3 0.6667 against 0.6664. Guardrail `PASS`; MLflow run `d81b7ea68cb84ca68c6f485676d2ecf5`; stage 6 read the six model files back from MinIO; stage 7 ran the CI model gate (all OK) and pushed commit `bd0fdd67` (the two classifiers and the manifest changed; the IsolationForest file did not, since it is fit on the fleet alone); stage 8 started the application pipeline, whose build #111 went green and deployed `devsecmlops-api:2.20.3-b111`. The +0.0001 is not an improvement and is not claimed as one (222 rows are 0.001% of the training set). What the run proves is the whole loop: operator verdicts in the database become a retrained, gated, committed and deployed model, with no manual step.

```bash
# Now provisioned by Ansible (roles/prerequisites, roles/monitoring) --
# manual commands kept here for reference / running outside Ansible:
docker run -d --name minio --restart=always \
  -p 9001:9000 -p 9002:9001 \
  -e "MINIO_ROOT_USER=${MINIO_ROOT_USER:?set first}" \
  -e "MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD:?set first}" \
  -v minio_data:/data \
  minio/minio server /data --console-address ":9001"

export MLFLOW_S3_ENDPOINT_URL=http://localhost:9001
export AWS_ACCESS_KEY_ID="${MINIO_ROOT_USER:?}"
export AWS_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD:?}"
mlflow server --host 0.0.0.0 --port 5001 \
  --backend-store-uri sqlite:///mlflow.db \
  --artifacts-destination s3://mlflow-artifacts/ --serve-artifacts
```

### Files

| File | What it does |
|---|---|
| `monitoring/production_agent.py` | The default traffic source — one thread per machine, real consecutive replay, real rolling history. |
| `monitoring/k8s_exporter.py` | Pod readiness, restarts, replica counts, HPA state. |
| `monitoring/prometheus.yml` | Scrape configuration — per-pod discovery through the Kubernetes API server. |
| `monitoring/grafana/provisioning/` | Fixed-UID datasource and dashboard-loading configuration, mounted read-only. |
| `monitoring/grafana/dashboards/devsecmlops-fleet.json` | The 30-panel dashboard, provisioned from the repository. |
| `monitoring/replay_exporter.py`, `anomaly_bridge.py` | Earlier traffic designs, kept and functional, not started by default. |
| `docs/machine_roster.txt` | All 200 machines: 40 web, 35 app, 30 db, 20 cache, 20 queue, 15 batch, 15 edge, 8 router, 7 firewall, 5 dns, 5 voip. |

---

## L6 — Ansible (Infrastructure as Code)

**Rewritten in the most recent infrastructure pass.** Two profiles
(`demo`, `production`), sharing common roles, bringing the entire platform
up from a provisioned VM with one command.

### What it is

#### Structure

```
ansible/
  inventory.ini            localhost
  group_vars/all.yml        images (VERSION-derived tag), ports, binary paths,
                              every credential read from the environment
  group_vars/demo.yml       demo profile toggles
  group_vars/production.yml production profile toggles
  site.yml                   the playbook: shared + profile-gated roles
  roles/
    system/                   OS packages, the Prometheus binary
    app_setup/                  venv, dependencies, the API image
    prerequisites/                registry, MinIO, MLflow, SonarQube, Jenkins
    secrets/                        production-only, Vault-backed DB Secret
    kubernetes/                       Minikube + manifests + readiness waits
    monitoring/                        the unified systemd monitoring stack
    control_panel/                      the mission-control API on :8000
```

#### Two profiles, what differs

**Production** (`group_vars/production.yml`): real Kubernetes Secrets from
an Ansible Vault value (a guard refuses to run with a placeholder
password), resource requests/limits on the serving Deployment, 3 replicas,
and no demo-only support containers or control panel — only the
`anomaly-api` workload runs.

**Demo** (`group_vars/demo.yml`, the jury-facing setup this repository runs
day to day): the full support tooling (registry, MinIO, SonarQube,
Jenkins), the production traffic agent enabled, and the `:8000`
mission-control panel — everything drivable from one browser tab.

#### What "production profile" means here

The `production` profile is a production-oriented configuration (Vault-backed credentials, resource limits, 3 replicas, no demo tooling) applied to the same single-node Minikube. It is not a deployment to a real production cluster.

#### Running it

```bash
export GRAFANA_ADMIN_PASSWORD='...'   # 12+ chars
export OPS_UI_PASSWORD='...'          # 12+ chars
export MINIO_ROOT_USER='...'
export MINIO_ROOT_PASSWORD='...'      # 12+ chars

cd ansible
ansible-playbook -i inventory.ini site.yml                          # demo (default)
ansible-playbook -i inventory.ini site.yml -e profile=production --ask-vault-pass

ansible-playbook -i inventory.ini site.yml --syntax-check
ansible-playbook -i inventory.ini site.yml --check                  # dry run
```

### What we built

#### Every host process is a supervised systemd unit, in both profiles

The original design had **two separate monitoring roles** —
`monitoring_systemd` (production: Prometheus and the exporters as managed
systemd units) and `monitoring` (demo: the same processes as bare `nohup`
background processes). This split has been removed. **Both profiles now use
one unified `monitoring` role**, and every host process — Prometheus, the
k8s-exporter, the production agent, the control panel, and MLflow — is a
systemd unit (`Restart=always`, enabled at boot) regardless of profile.

This was not a preemptive tidy-up: during this project's own development, a
VM reboot took down the entire demo monitoring stack with no automatic
recovery, because the demo profile's plain background processes do not
survive a reboot. The unified, systemd-everywhere design closes that gap.

#### MLflow provisioned and supervised

MLflow runs as a systemd service (`:5001`) with `--serve-artifacts`,
proxying uploads to MinIO — see [MLflow and MinIO](#mlflow-and-minio-experiment-tracking-artifact-store)
above for the real round-trip verification. It was not consistently running
before this pass.

#### Credentials from the environment, root-only files

Every credential — the Grafana admin password, the ops-UI Basic-auth
credentials, the MinIO root credentials — is required from the environment
at playbook run time (a 12-character-minimum `assert`), written to
root-only files (`0600`, under `/etc/devsecmlops/`), and never committed or
placed directly in a systemd unit file. The PostgreSQL Secret is assembled
in Ansible with base64-encoded values and applied to Kubernetes via
`stdin` — the password never appears on a command line or in the process
list.

#### Jenkins gets the data mount it needed

The Jenkins container now has `data/` mounted read-only at
`/var/jenkins_home/fleet-data` — the prerequisite `Jenkinsfile.model`
needed and did not have before this pass. The Jenkins image tag, and the
API image tag, both now derive from the `VERSION` file rather than
separate hardcoded values that had drifted out of sync with each other.

#### Design notes

- **SonarQube hostname**: from the host, `http://localhost:9000`; from
  inside the Jenkins container, `http://sonarqube:9000` (Docker resolves
  the container name). Both correct in their respective contexts.
- **Image loading**: the API image is loaded into Minikube directly
  (`minikube image load`, `imagePullPolicy: Never`) rather than pulled from
  the local registry, for the same single-node reasons documented in
  [L4 — Kubernetes](#l4--kubernetes).
- **Scope**: Ansible automates the *platform bring-up*. The `system` role
  installs Docker, kubectl, Minikube and Prometheus when they are missing
  (pinned versions, skipped when present). It does not codify Jenkins and
  SonarQube settings (jobs, credentials, plugins, project, token), which live
  in Docker volumes and were set up by hand. The step-by-step procedure for a
  new machine, with what is verified and what is not, is in
  [docs/INFRASTRUCTURE_SETUP.md](docs/INFRASTRUCTURE_SETUP.md); it has not been
  run on a clean second machine.

### What we tested

#### Verified with a real playbook run, not just a syntax check

`--syntax-check` on both profiles, then a `--check` dry run, then a real
run reaching `PLAY RECAP ... failed=0, ok=56, changed=12`. Confirmed
afterward against the live control panel: **11 of 11 platform layers up**,
including MLflow running for the first time in this project's development,
and confirmed with direct checks that the auth enforcement (`401` with no
credentials, `401` with a wrong password, `200` only with the real one) and
service persistence (`systemctl is-enabled` reporting `enabled` for every
new unit) genuinely work, not just that the playbook exited successfully.

### Real bugs found and fixed

#### `repo_dir` was not a normalized path (found only by running it)

`repo_dir` was defined as `"{{ playbook_dir }}/.."`, which resolves to an
unnormalized path (e.g. `.../ansible/..`). systemd rejects this in
`WorkingDirectory=` ("path is not normalized"). Fixed with a `| realpath`
filter. This would have affected every systemd unit this project installs,
not only MLflow — it was only caught here because MLflow was the first
genuinely *new* unit this pass introduced; every pre-existing unit had
silently carried the same latent bug without being re-validated.

#### Support containers never declared their network (found by a failed CI build)

`registry`, `minio`, and `sonarqube` were defined in `roles/prerequisites` with ports, volumes, and restart policy, but **no `networks:` key at all** — so `community.docker.docker_container` placed each one on Docker's default `bridge` network every time it (re)created them. Jenkins reaches all three by container name over `devsecmlops-net`, so after an Ansible run recreated them, a Jenkins build failed with `sonarqube: Name or service not known`. Confirmed live before fixing: `docker inspect` showed all three on `bridge` only; `docker exec jenkins getent hosts sonarqube` returned nothing. Fixed by declaring `networks: [devsecmlops-net]` on all three tasks. Verified with a real playbook run (not `--check`): all three now on `devsecmlops-net`, and a live DNS lookup from inside the Jenkins container resolves `registry`, `sonarqube`, and `minio` by name. The next Jenkins build passed end to end.

#### Ansible could not fail on the steps that mattered

Four tasks in `roles/kubernetes` had `failed_when: false`: enabling metrics-server, loading the API image, and both readiness waits. A broken image load (`ErrImageNeverPull`) or a PostgreSQL pod that never became Ready would still end the play with `failed=0`. Now every one of them fails the play. The image load is additionally verified with `minikube image ls`, because this project has already seen `minikube image load` exit 0 without loading anything. The waits use `rollout status`, which tracks the new revision rather than any pod carrying the label. The only remaining tolerated non-zero exits are the two genuinely expected ones (Minikube stopped, Deployment not yet created), each commented in place.

#### Ansible rolled back Jenkins releases

The rendered Deployment used the Ansible default tag (`devsecmlops-api:<VERSION>`), so re-running the playbook replaced whatever Jenkins had deployed (e.g. `2.20.3-b87`) with an older locally built image. Ownership is now explicit: Ansible creates the Deployment on first bring-up; once it exists, Ansible keeps the currently deployed image and Jenkins owns releases. Verified live: Jenkins deployed `2.20.3-b88`; a full demo-profile Ansible run then finished with `failed=0`; the Deployment was still on `2.20.3-b88` and its rollout history was unchanged (revision 2, Jenkins's deploy). Ansible re-applied the Deployment without rolling a single pod.

### Files

| File / role | What it does |
|---|---|
| `ansible/site.yml` | Playbook entry point — resolves the profile, runs shared and profile-gated roles. |
| `ansible/inventory.ini` | `localhost`. |
| `ansible/group_vars/all.yml` | Shared variables: VERSION-derived image tag, ports, every credential lookup from the environment. |
| `ansible/group_vars/demo.yml`, `production.yml` | Per-profile toggles. |
| `roles/system/` | OS packages, the Prometheus binary. |
| `roles/app_setup/` | Python virtualenv, dependencies, the API image. |
| `roles/prerequisites/` | Registry, MinIO, MLflow, SonarQube, Jenkins — all on `devsecmlops-net`. |
| `roles/secrets/` | Production-only, Vault-backed DB Secret applied via stdin. |
| `roles/kubernetes/` (+ `templates/deployment.yaml.j2`) | Minikube, the demo DB Secret, the rendered Deployment — now in sync with `kubernetes/deployment.yaml`. |
| `roles/monitoring/` | The unified systemd monitoring stack. |
| `roles/control_panel/` | The mission-control API on :8000, credentials in a root-only file. |

---

## Security posture

Every hardening measure across the platform, in one place:

| Area | Measure |
|---|---|
| Credentials | Nothing committed. PostgreSQL, Grafana, MinIO, and the ops-UI password are all generated randomly or supplied from the environment at deploy time. A real committed password (`feedback-dev-password`, in the now-deleted `kubernetes/postgres-secret.yaml`) was found, removed, and rotated live in this VM's actual running database — see [Storage](#storage) above for the full rotation story. |
| Containers | Both the API and PostgreSQL run as non-root, with `readOnlyRootFilesystem`, all Linux capabilities dropped, `seccompProfile: RuntimeDefault`, and `automountServiceAccountToken: false`. |
| Operations console | `/ui/*` does not exist (404) unless `ENABLE_OPS_UI=1` is explicitly set — the Kubernetes serving pods never set it. When enabled, it requires HTTP Basic auth, compared in constant time, and fails closed (`503`) if enabled without a password configured. This console previously had no authentication of any kind. |
| Supply chain | Every production model artifact's SHA-256 is verified against `models/manifest.json` before it is unpickled — both in the API and in CI. A CycloneDX SBOM is generated and archived on every image build. |
| Model governance | The CI model gate refuses a build unless the artifact hashes, the recorded F1, the deployed threshold (selected for exactly these artifacts on an independent validation fleet) and `MODEL_NAME` all agree with `models/manifest.json`; the API refuses to start if any production artifact is missing or modified. |
| Image | Python 3.12 on Debian trixie (was 3.10-slim); `pip` and `ensurepip` are removed from the final image after dependencies are installed; Debian security updates are applied on every build (a cached upgrade layer once shipped an already-fixed OpenSSL, caught by the image scan). |
| Input handling | Path traversal in the training console's dataset selector closed. `NaN`/`infinity` rejected at the API boundary. `history` validated strictly (exact keys, exact length). |
| Scanning | Trivy scans both the repository (dependencies, committed secrets, Kubernetes/Dockerfile misconfigurations) and the built image, both blocking (`--exit-code 1`) in CI. |
| Secrets in transit | The PostgreSQL Secret is assembled by Ansible and applied via `stdin`, never a command line or a shell pipe. |

---

## Engineering decisions and rationale

Every non-obvious decision made in this project, why it was made that way, and what evidence supported the choice. Written to be readable in isolation for defense preparation -- each subsection explains one decision from first principles rather than referencing sections elsewhere in the document.

### 1. Why unsupervised anomaly detection first (v1)

Production infrastructure has no labeled anomalies. Nobody manually labels every anomalous minute across 200 servers -- and even if they did, the labels would be inconsistent across engineers. IsolationForest learns "normal" from the raw metric stream with zero labels, and can flag anomaly *types* it has never seen before. This was the right starting choice: build a working, deployable detector without waiting for a labeling pipeline that doesn't exist in real telecom operations.

### 2. Why per-machine, per-time-window baselines

A web server idling at 30% CPU and a database server running hot at 75% CPU are both "normal" for their role -- a global threshold cannot distinguish them. Time windows (night/morning/afternoon/evening) handle predictable daily cycles: a 3 AM CPU spike on a batch server is normal, the same spike at 3 PM might be an incident. Tested rigorously (`ml-model/test_timewindow_full.py`): per-machine+window F1 = 0.6475, per-machine all-day F1 = 0.6434, adding explicit time features (hour_sin/cos) actually hurt at F1 = 0.6064 because the per-machine baseline already encodes temporal patterns implicitly.

### 3. Why 6 features, not 3

The original 3 features (cpu, ram, network) missed entire classes of real incidents. A disk filling up produces almost no signal in cpu/ram/network -- confirmed live: a disk_saturation reading with disk_usage z=4.93 and load_avg z=5.31 while cpu/ram/network stayed near zero. Expanded to 6 (adding disk_io, disk_usage, load_avg). Network gear (routers, firewalls, DNS, VoIP) intentionally have near-zero disk metrics because they are SNMP-monitored appliances with no physical disk -- this matches real telco edge architecture, not a shortcut.

### 4. Why 30-second resolution, not 1-minute

Tested at full scale: F1 improved 0.648 -> 0.663, recall 0.650 -> 0.667. Real gains, reproducible. Cost: 2x storage (613MB -> 1.2GB), 2x retrain time (2m -> 4m), and the first attempt to generate the 30-second dataset was killed by the OOM killer at 4.1GB RSS on the 7.7GB VM. Adopted because the accuracy gain is consistent and the memory constraint is manageable -- but the tradeoff is documented explicitly, not glossed over.

### 5. Why the four-level fallback chain

Machines the model has never seen (`machine+window` not in baseline) must still get predictions. The chain -- `machine+window` -> `machine` (all-day) -> `machine type` -> `global fleet average` -- guarantees any legitimate reading gets a valid baseline. The API returns which level was used (`baseline_used` field), so operators can see whether a prediction is on solid statistical ground or falling back to a coarser estimate.

### 6. Why the service-tier correlation was tested and rejected

The obvious next step after modeling router->machine dependencies was modeling application-tier calls (web -> app -> db). Built, tested at full scale, and reverted: every model regressed (IsolationForest 0.663 -> 0.599, z-threshold 0.657 -> 0.572). Root cause: service-tier correlation inflated the variance absorbed into each downstream machine's baseline, particularly for types 1-2 hops away, widening what counts as "normal" and making genuine anomalies harder to distinguish. **This is a real, informative negative result** -- documented rather than silently discarded, because knowing what doesn't work matters.

### 7. Why novelty detection (train on normal-only) was tested and rejected
A natural alternative for the unsupervised baseline is *novelty detection*:
train the IsolationForest on confirmed-normal rows only (excluding every
anomaly from training), so it learns the shape of "normal" and flags any
deviation. Implemented in `ml-model/train_serving_telecom_novelty.py` and run
at full scale (11.3M normal-only training rows, evaluated on the same seed-42
mixed test split): F1 = 0.564, Precision = 0.448, Recall = 0.764, ROC-AUC =
0.921. Training on normal-only *raises* recall (0.764 vs the mixed-training
0.650) — the model is more sensitive because it has never been shown that
some anomaly-adjacent readings are tolerable — but precision *collapses*
(0.448 vs 0.646): it over-flags, because it never learned where the boundary
of acceptable near-normal variation lies. Net F1 drops from 0.648 to 0.564,
while ROC-AUC is essentially unchanged (0.921 vs 0.924), confirming the
ranking ability is similar and only the operating point moved. **Another
informative negative result**: pure novelty detection is not adopted, because
the mixed-training baseline plus a supervised classifier on top gives better
balanced performance. The experiment is kept because it quantifies exactly
what novelty detection costs on this data.

### 8. Why supervised (v2 RandomForest) added on top, not replacing v1

IsolationForest tells you *something is wrong* but not *what*. Adding labeled cause data (`anomaly_type` column: cpu_spike, memory_leak, network_flood, disk_saturation, silent_failure, cascade) enabled a supervised classifier to explain the anomaly. RandomForest was chosen over other supervised options because it needs no feature scaling assumptions, handles the 6-feature space cleanly, and is deterministic. Result on independent seed-123 test set: F1 = 0.731 (vs IsolationForest's 0.652). But cascade recall collapsed to 0.029 -- see next decision.

### 9. Why cascade is folded into normal during training

The generator by design labels cascade anomalies only 40% consistently (a downstream machine affected by a router failure is labeled anomalous 40% of the time, unlabeled 60%, *for the identical feature pattern*). Training a supervised classifier on this noisy label collapses everything -- initial F1 = 0.513, cascade precision = 0.16, poisoning the other classes. Folding cascade into normal during training fixed that (F1 = 0.731) but left RandomForest blind to cascades. Solution: keep IsolationForest as a *safety net* (see next decision).

### 9b. Audit finding — the generator does not mark the router that causes a cascade

Decision 9 covers the cascade label being noisy by design. Auditing the generator surfaced a
second, independent problem: **a router's own anomaly timeline is unrelated to the cascades it
drives.** In `ml-model/generate_telecom_fleet.py` each router is generated twice — once to build
the mask that propagates to its dependents (that frame is then discarded), and again, at a
different `rng` state, to produce the router's own rows. Measured on the full training fleet with
`scripts/cascade_check.py`:

```console
$ python3 scripts/cascade_check.py
    router | own rate | corr(own, dep share) | cascade events | router anomalous then
----------------------------------------------------------------------------------------
 router-01 |   0.0501 |               0.0009 |          4,247 |                0.0626
 router-02 |   0.0501 |               0.0053 |          4,166 |                0.0480
 router-03 |   0.0500 |               0.0001 |          4,278 |                0.0423
 router-04 |   0.0507 |              -0.0236 |          4,274 |                0.0241
 router-05 |   0.0503 |              -0.0022 |          4,331 |                0.0566
 router-06 |   0.0502 |              -0.0055 |          4,295 |                0.0536
 router-07 |   0.0503 |               0.0290 |          4,262 |                0.0878
 router-08 |   0.0503 |              -0.0070 |          4,210 |                0.0359

mean correlation across routers: -0.0004
during cascade events the router is anomalous 0.0514 of the time; its base rate is 0.0502
ratio 1.02x  (1.0x = the cascade tells you nothing about the router)
```

Mean correlation **-0.0004**, and during a cascade the router is anomalous **1.02x** its base
rate — i.e. no more often than chance. The cascades themselves are real and plentiful (~4,250
events per router, ~34,000 across the fleet): the dependents of one router genuinely fail
together. Only the router's own label fails to mark them.

**Consequences.** `score_root_causes` (`ml-model/root_cause.py`) accumulates downstream effects
only for a machine that is itself in the flagged set, so for a generator-produced cascade it never
names the router; the Root Cause tab demonstrates the logic on a constructed input (a router plus
its dependents, flagged together), not on a pattern the data produces on its own. This also
compounds Decision 9: cascade is hard to learn both because its label is 40% consistent *and*
because the one signal that would explain it — the upstream router's state — is absent from the
data as well.

**Deliberately not fixed before the defense.** The correction is one line (reuse the first draw
instead of regenerating the router), but it invalidates every dataset, both classifiers, the
IsolationForest, the selected threshold, the guardrail baseline and every number in this README.
It is recorded here rather than quietly patched.

**Update.** The fix was then built as a separate generator (v3) and tested at full size against
production; see [Decision 9c](#9c-failure-model-v3--router-incidents-modelled-the-way-networks-actually-fail). The shipped data and models are unchanged.

One caveat against over-reading it: a router degrading traffic while its own SNMP counters stay
healthy is a real operational phenomenon, which is exactly why root-cause attribution leans on the
dependency graph instead of the router's own readings. That resemblance is a coincidence, not the
original intent.

### 9c. Failure model v3 — router incidents modelled the way networks actually fail

Decision 9b found that the v2 generator never marks the router that causes a cascade. The fix
is a separate generator, `ml-model/generate_telecom_fleet_v3.py`, built so that every pattern a
reviewer can point at has a mechanism behind it and a measured count. It was then tested at full
size against production (`scripts/v3_experiment.sh`). **Result: production stays on its v2-trained
models.** A candidate trained on 30 days of v3 data was rejected by the guardrail, and the
precision-recall curves below show the guardrail made the right call under the current policy.
v3 remains in the repository as the more realistic generator, the data the Fleet tab's reasoning
is measured on, and the basis for the next feature (a TCP retransmission feature, see "What it says").

Vocabulary: a **router group** is one router plus the machines that depend on it in
`models/dependency_graph.json` (in a data centre this is a rack behind its top-of-rack switch).
The **group rule** is the Fleet tab's logic in `api/fleet_rules.py`, which judges a router from
the verdicts of its whole group; the scripts call it the "rack rule".

**What real networks do, and where each rule comes from**

| Observation from production networks | Source | What v3 does with it |
|---|---|---|
| A component can be degraded while its own monitoring says it is healthy: "differential observability". Silent packet drops cause application glitches and latency, and data-centre networks do not reroute around them. | Huang et al., *Gray Failure: The Achilles' Heel of Cloud-Scale Systems*, HotOS 2017 | **Gray** incident: the router's own CPU/RAM stay near normal, its forwarded throughput falls, its dependents degrade. |
| Silent drops are "nearly impossible to detect with traditional monitoring tools (e.g., SNMP)"; the signal is seen at the hosts, as TCP retransmissions. | Arzani et al., *007: Democratically Finding the Cause of Packet Drops*, NSDI 2018 | The evidence of a gray failure is in the dependents, not in the router's counters. |
| TCP throughput falls as packet loss rises: bandwidth ∝ MSS/(RTT·√p). | Mathis et al., *The Macroscopic Behavior of the TCP Congestion Avoidance Algorithm*, CCR 1997 | Dependents' network throughput **drops** under a gray failure. (v2 *doubled* it, which is backwards.) |
| Median traffic during a failure is 65% of normal per link; most link failures are resolved within five minutes; ToRs fail less than 5% per year. | Gill et al., *Understanding Network Failures in Data Centers*, SIGCOMM 2011 | Gray throughput loss centred near 65%; incident durations short-tailed (median 5 min, up to 1 h); rates compressed and stated. |
| When a parent router is down, its children are reported UNREACHABLE, not DOWN, and only the parent raises an alert. | Nagios parent/child reachability | **Down** incident: router and all dependents go silent at once (`reachable=0`). |
| An unreachable scrape target shows up as `up == 0` or missing data, not as a high value. | Grafana alerting guide, connectivity errors | Unreachable machines are not "anomalous metrics"; the live agent sends nothing for them. |
| Cascades build up through positive feedback, retries and resource exhaustion, over about a couple of minutes. | Beyer et al., *Site Reliability Engineering*, ch. 22 "Addressing Cascading Failures" | Dependents degrade 30 s to 3 min after the router and recover with a short tail. |
| Linux load average counts runnable and uninterruptible (disk-blocked) tasks. | B. Gregg, *Linux Load Averages: Solving the Mystery*, 2017 | Load rises only a little under network loss (socket waits are not disk waits). |
| Fault-injection benchmarks inject a fault into a randomly chosen service for each case and label its root-cause service. | Pham et al., *RCAEval*, 2024 | Every anomalous reading carries `root_cause`; the incident log is the ground truth. |

**The model**

- **Three router incident modes.** *Gray* (silent loss, 55% of incidents), *down* (crash or lost
  uplink, 30%), *control plane* (the router's management CPU spikes but forwarding is unaffected,
  15%). The split is an assumption informed by Gill et al. (most failures are partial); no
  published study gives it exactly.
- **One schedule drives the router and its dependents**, so the router is labelled during every
  one of its incidents (Decision 9b's defect is gone).
- **Impact depends on the machine and the hour.** Chatty or latency-sensitive tiers (web, app,
  cache, edge, voip) are hit hardest, batch least; impact scales with current traffic, so the same
  gray failure hurts far fewer machines at night.
- **Labels follow impact, deterministically.** A dependent is labelled anomalous only if its impact
  is material. Decision 9's coin flip (the same degraded state labelled anomalous 40% of the time)
  is gone.
- **Two new columns.** `root_cause` (the machine that caused the reading's anomaly) and `reachable`.
  The v2 columns are unchanged, so the current pipeline can read a v3 file.

**How the numbers below were produced.** One script, `scripts/v3_experiment.sh`, run as a
transient systemd unit under the 11 GB memory cap used for full-size training. It generates the
four v3 fleets with the same sizes and seeds as v2 (200 machines; training 30 days seed 42,
validation 14 days seed 7, test 30 days seed 123, operator 3 days seed 2026), checks their realism,
trains the real pipeline (`train_production.py --promote`) in a scratch folder, scores the group
rule, and compares. Production's artifacts are hashed before and checked after (unchanged). Every
block below is the script's output, copied verbatim from `build/v3/`.

```bash
systemd-run --unit=v3-experiment -p MemoryMax=11G -p MemorySwapMax=0 \
    --working-directory="$PWD" /bin/bash scripts/v3_experiment.sh
START=6 bash scripts/v3_experiment.sh     # only steps 6-7, reusing the fleets and the training report
```

**Measured realism** (step 2, training fleet: 17,280,000 rows, 1,510 router incidents):

```console
$ python3 scripts/failure_model_report.py --data data/telecom_fleet_v3_labeled.csv
== 1. Incidents (ground truth) ==
  gray             831   duration median 5.0 min, p95 18.2 min
  down             458   duration median 5.0 min, p95 18.6 min
  control_plane    221   duration median 5.0 min, p95 14.0 min

== 2. Share of a router's dependents affected at the incident's peak ==
  mode           when       n    min  median    max
  gray           day      432     0%     78%   100%
  gray           night    399     0%      0%    91%
  down           day      219    78%     96%   100%
  down           night    239    82%     96%   100%
  control_plane  day      120     0%      0%     0%
  control_plane  night    101     0%      0%     0%
  hard down, dependents UNREACHABLE at the peak: min 100%, median 100% (below 100% affected only where a machine was already failing for its own reason)

== 3. Propagation delay (incident start -> first affected dependent) ==
  gray   median 60 s, range 30-1140 s
  down   median 0 s, range 0-0 s

== 4. "15+ machines behind one router are anomalous and the router is normal" ==
  router-steps with 15+ anomalous dependents: 10,182
  ... of which the router is labelled normal:         449
      in the recovery tail just after an incident:     449
      not explained by an incident (coincident local faults): 0

== 5. "every machine behind a router is anomalous" ==
  during down           6,080
  during gray             272
  unreachable dependents outside any hard-down incident or its tail: 0

== 6. "the router is anomalous and its machines are fine" ==
  control_plane (data plane unaffected)      2,728 of 2,728 router-anomalous steps
  gray                                       4,771 of 11,603 router-anomalous steps
  down                                           0 of 6,538 router-anomalous steps
    gray, within the first 3 min (propagation lag): 2,839
    gray, later (impact below materiality: low traffic or mild loss): 1,932
```

The generator's propagation lag is 30–180 s; longer gray delays (up to 1,140 s) are incidents
whose impact stays below materiality until traffic rises, since impact scales with current traffic. In the "down" rows, a peak below 100% *affected* means a machine was already
failing for its own reason; 100% of dependents are *unreachable* in every hard-down incident.

**Coupling between a router's own label and its dependents** (`scripts/cascade_check.py`, both
on full 30-day training fleets; the v2 output is in Decision 9b):

| | v2 (shipped data) | v3 |
|---|---|---|
| mean correlation, router label vs share of dependents anomalous | -0.0004 | **+0.6691** (per router 0.645 to 0.731) |
| router anomalous during a cascade | 5.14% (base rate 5.02%) → 1.02× | 87.14% (base rate 3.02%) → **28.86×** |

```console
$ python3 scripts/cascade_check.py --data data/telecom_fleet_v3_labeled.csv
    router | own rate | corr(own, dep share) | cascade events | router anomalous then
----------------------------------------------------------------------------------------
 router-01 |   0.0301 |               0.6506 |          1,705 |                0.8557
 router-02 |   0.0301 |               0.6528 |          1,706 |                0.8417
 router-03 |   0.0307 |               0.6492 |          1,617 |                0.8930
 router-04 |   0.0304 |               0.6536 |          1,686 |                0.8802
 router-05 |   0.0301 |               0.6445 |          1,654 |                0.8797
 router-06 |   0.0301 |               0.7307 |          2,000 |                0.8765
 router-07 |   0.0300 |               0.6722 |          1,777 |                0.8807
 router-08 |   0.0301 |               0.6996 |          1,887 |                0.8649

mean correlation across routers: +0.6691
during cascade events the router is anomalous 0.8714 of the time; its base rate is 0.0302
ratio 28.86x  (1.0x = the cascade tells you nothing about the router)
```

The router is anomalous in 87%, not 100%, of cascade steps mostly because dependents recover a
little after the router does (the recovery tail of section 4 above).

**Accuracy: production versus a v3-trained candidate** (step 3 trains the candidate on the 30-day
v3 training fleet, 17,280,000 rows, IsolationForest contamination 0.06311; the policy selects its
threshold on the v3 validation fleet: 0.32, recall 0.8569, precision 0.7268. Step 5 prints):

```console
$ python3 scripts/compare_v2_v3.py --report build/v3/report.json

== served decision, v4 path ==
                              thr      F1    prec  recall |  clf F1   clf P   clf R | cause acc
A production on v2 test      0.91  0.7291  0.6713  0.7979 |  0.8100       —       — |    0.9983
B production on v3 test      0.91  0.8027  0.7324  0.8880 |  0.8981  0.9149  0.8819 |    0.8572
C v3 candidate on v3 test    0.32  0.7901  0.7266  0.8658 |  0.9149  0.9868  0.8528 |    0.9797

== served decision, v3 path ==
                              thr      F1    prec  recall |  clf F1   clf P   clf R | cause acc
A production on v2 test      0.91  0.6664  0.5967  0.7546 |  0.7119       —       — |    0.9938
B production on v3 test      0.91  0.7359  0.6555  0.8387 |  0.7977  0.7669  0.8311 |    0.8368
C v3 candidate on v3 test    0.32  0.7368  0.7009  0.7765 |  0.8356  0.9637  0.7376 |    0.9341

== per-cause recall, v4 path ==
cause                          A         B         C
cascade                   0.2679         —         —
cascade_gray                   —    0.1046    0.1695
cascade_unreachable            —    0.9775    0.9652
cpu_spike                 0.9879    0.9859    0.9640
disk_saturation           0.9967    0.9966    0.9899
memory_leak               0.9333    0.9355    0.8223
network_flood             0.9945    0.9940    0.9863
router_down                    —    0.7791    0.9098
router_gray                    —    0.0800    0.1771
silent_failure            0.9810    0.9805    0.9487

== fair comparison (B vs C: same v3 test fleet) ==
  f1        production 0.8027   v3 candidate 0.7901   difference -0.0126
  precision production 0.7324   v3 candidate 0.7266   difference -0.0058
  recall    production 0.8880   v3 candidate 0.8658   difference -0.0222

guardrail on the validation fleet: REJECTED
  - v4 recall 0.8780 -> 0.8569
  - v4 cpu_spike recall 0.9856 -> 0.9629
  - v4 memory_leak recall 0.9345 -> 0.8239
  - v4 silent_failure recall 0.9806 -> 0.9479
  - v3 recall 0.8310 -> 0.7695
  - v3 cascade_gray recall 0.1170 -> 0.0428
  - v3 cascade_unreachable recall 0.9793 -> 0.9297
  - v3 cpu_spike recall 0.9042 -> 0.8323
  - v3 disk_saturation recall 0.9839 -> 0.9627
  - v3 memory_leak recall 0.7717 -> 0.6643

candidate trained on 17,280,000 rows
```

(The full rejection list in the training log has two more lines: v3 router_down 0.8889 → 0.7767,
v3 silent_failure 0.9173 → 0.8108.)

Production in this comparison is the model served from 19 to 21 October (build 8). The 21 October
retrain with feedback (build 9) differs from it by +0.0001 v4 F1 on the v2 test fleet; the comparison
was not rerun.

**Is that a threshold effect?** The two models run at different thresholds (0.91 and 0.32), so step
6 sweeps both over the policy's full grid (95 thresholds, 0.05 to 0.99) on the same v3 validation
fleet and compares them at equal recall:

```console
$ python3 scripts/compare_curves.py --report build/v3/report.json --out build/v3/production_curve.json
sweeping production on telecom_fleet_v3_val.csv: 95 thresholds, 0.05 -> 0.99

  thr | production  P       R      F1 | v3 candidate  P       R      F1
 0.05 |        0.7552  0.7977  0.7759 |          0.7207  0.8117  0.7635
 0.10 |        0.7579  0.8120  0.7840 |          0.7239  0.8282  0.7726
 0.15 |        0.7592  0.8203  0.7885 |          0.7255  0.8379  0.7777
 0.20 |        0.7600  0.8262  0.7917 |          0.7263  0.8447  0.7810
 0.25 |        0.7604  0.8310  0.7941 |          0.7267  0.8503  0.7837
 0.30 |        0.7608  0.8351  0.7962 |          0.7268  0.8552  0.7858
 0.32 |        0.7608  0.8367  0.7969 |          0.7268  0.8569  0.7865
 0.35 |        0.7609  0.8388  0.7979 |          0.7267  0.8594  0.7875
 0.40 |        0.7610  0.8421  0.7995 |          0.7264  0.8634  0.7890
 0.45 |        0.7608  0.8452  0.8008 |          0.7259  0.8672  0.7903
 0.50 |        0.7605  0.8481  0.8019 |          0.7251  0.8708  0.7913
 0.55 |        0.7601  0.8509  0.8029 |          0.7239  0.8745  0.7921
 0.60 |        0.7595  0.8537  0.8038 |          0.7223  0.8783  0.7927
 0.65 |        0.7585  0.8566  0.8046 |          0.7200  0.8824  0.7930
 0.70 |        0.7572  0.8597  0.8052 |          0.7167  0.8870  0.7928
 0.75 |        0.7551  0.8631  0.8055 |          0.7117  0.8919  0.7917
 0.80 |        0.7519  0.8668  0.8053 |          0.7039  0.8977  0.7891
 0.85 |        0.7465  0.8710  0.8040 |          0.6894  0.9047  0.7825
 0.90 |        0.7356  0.8766  0.7999 |          0.6605  0.9145  0.7671
 0.91 |        0.7321  0.8780  0.7984 |          0.6511  0.9171  0.7615
 0.95 |        0.7053  0.8856  0.7852 |          0.5811  0.9313  0.7156

best F1 on each curve: production 0.8055 at 0.74 | candidate 0.7930 at 0.65
highest recall reached: production 0.9062 | candidate 0.9687
at recall >= 0.80: production P 0.7610 (thr 0.37) | candidate P 0.7268 (thr 0.26)
at recall >= 0.85: production P 0.7602 (thr 0.54) | candidate P 0.7268 (thr 0.26)
at recall >= 0.90: production P 0.5479 (thr 0.99) | candidate P 0.6992 (thr 0.82)
the candidate becomes the more precise detector from recall 0.89 upwards; below that, production is
```

No: at every recall up to 0.88, production is about 3 points more precise whatever the threshold.
The candidate wins only above 0.89 recall, a level production cannot reach precisely (0.548 at
0.90) and never passes 0.906. Under the current policy (`min_recall` 0.80) production is the better
model; a policy demanding 90% recall would choose the candidate.

**Group level** (step 4: each model scores every test reading, then the Fleet tab's exact rule from
`api/fleet_rules.py` runs at every 30-second step; 845 gray and 453 hard-down incidents in the test
fleet):

```console
$ python3 scripts/rack_rule_eval.py --data data/telecom_fleet_v3_test.csv --models build/v3/candidate --threshold 0.32
threshold 0.32, rule: 40% of reporting dependents, at least 4 machines
per machine: the served decision flags 17.0% of gray-cascade readings (16,599 of 97,907)
per incident, gray: router named as suspect in 85 of 845 (10%); median 300 s after the incident starts
per incident, down: router named as suspect in 453 of 453 (100%); median 30 s after the incident starts
false alarms: 302 router-steps flagged outside any incident, of 664,210 (0.045%; 1.3 per router-day)

$ python3 scripts/rack_rule_eval.py --data data/telecom_fleet_v3_test.csv --models models
threshold 0.91, rule: 40% of reporting dependents, at least 4 machines
per machine: the served decision flags 10.5% of gray-cascade readings (10,238 of 97,907)
per incident, gray: router named as suspect in 45 of 845 (5%); median 360 s after the incident starts
per incident, down: router named as suspect in 453 of 453 (100%); median 30 s after the incident starts
false alarms: 159 router-steps flagged outside any incident, of 664,210 (0.024%; 0.7 per router-day)
```

**Can the rule catch more gray failures?** Step 7 sweeps the two knobs of the rule: the evidence
window (a machine counts as flagged if the model flagged it at least once in its last W readings)
and the share of the router's reporting dependents that must be flagged (at least 4 machines):

```console
$ python3 scripts/rack_rule_sweep.py --data data/telecom_fleet_v3_test.csv --models build/v3/candidate --threshold 0.32
threshold 0.32; a router is named when the rule says ['gray_suspected', 'router_incident', 'unreachable']
  window  share |     gray named  median lag |     down named |  false alarms / router-day
    30 s    20% |   244/845   29%       180 s |   453/453  100% |                       81.3
    30 s    25% |   177/845   21%       210 s |   453/453  100% |                       35.4
    30 s    30% |   140/845   17%       240 s |   453/453  100% |                       15.6
    30 s    40% |    85/845   10%       300 s |   453/453  100% |                        1.3  <- shipped
   120 s    20% |   451/845   53%       150 s |   453/453  100% |                      372.0
   120 s    25% |   342/845   40%       180 s |   453/453  100% |                      220.3
   120 s    30% |   271/845   32%       210 s |   453/453  100% |                      152.4
   120 s    40% |   176/845   21%       240 s |   453/453  100% |                       93.8
   180 s    20% |   524/845   62%       120 s |   453/453  100% |                      608.0
   180 s    25% |   419/845   50%       150 s |   453/453  100% |                      369.9
   180 s    30% |   332/845   39%       180 s |   453/453  100% |                      235.6
   180 s    40% |   201/845   24%       210 s |   453/453  100% |                      128.0
   240 s    20% |   576/845   68%        60 s |   453/453  100% |                      849.2
   240 s    25% |   487/845   58%       120 s |   453/453  100% |                      556.9
   240 s    30% |   390/845   46%       165 s |   453/453  100% |                      361.6
   240 s    40% |   236/845   28%       210 s |   453/453  100% |                      157.3

$ python3 scripts/rack_rule_sweep.py --data data/telecom_fleet_v3_test.csv --models models
threshold 0.91; a router is named when the rule says ['gray_suspected', 'router_incident', 'unreachable']
  window  share |     gray named  median lag |     down named |  false alarms / router-day
    30 s    20% |   217/845   26%       180 s |   453/453  100% |                       75.5
    30 s    25% |   136/845   16%       210 s |   453/453  100% |                       29.7
    30 s    30% |    98/845   12%       270 s |   453/453  100% |                       11.6
    30 s    40% |    45/845    5%       360 s |   453/453  100% |                        0.7  <- shipped
   120 s    20% |   458/845   54%       150 s |   453/453  100% |                      352.2
   120 s    25% |   347/845   41%       180 s |   453/453  100% |                      204.0
   120 s    30% |   252/845   30%       210 s |   453/453  100% |                      141.2
   120 s    40% |   138/845   16%       240 s |   453/453  100% |                       81.8
   180 s    20% |   530/845   63%       120 s |   453/453  100% |                      569.1
   180 s    25% |   430/845   51%       150 s |   453/453  100% |                      329.8
   180 s    30% |   330/845   39%       180 s |   453/453  100% |                      208.4
   180 s    40% |   169/845   20%       240 s |   453/453  100% |                      122.4
   240 s    20% |   589/845   70%        90 s |   453/453  100% |                      808.2
   240 s    25% |   485/845   57%       120 s |   453/453  100% |                      494.8
   240 s    30% |   390/845   46%       180 s |   453/453  100% |                      307.2
   240 s    40% |   196/845   23%       240 s |   453/453  100% |                      144.8
```

`rack_rule_sweep.py` uses `fleet_rules.classify_arrays`, a vectorised copy of the API's rule that
`tests/test_rack_tools.py` checks against `classify()` on 5,000 random cases; its shipped row
matches `rack_rule_eval.py` exactly (85/845, 1.3 per router-day; 45/845, 0.7).

**What it says**

1. **Production stays, and the governance worked.** A model trained on more realistic data was
   *not* better: lower F1 (-0.013), recall (-0.022) and precision (-0.006) on the same test fleet,
   and less precise at every recall up to 0.88. The guardrail rejected it on the validation fleet,
   not on the test numbers. Promotion is decided by measurement, not by the
   assumption that newer or more realistic data trains a better model.
2. **The candidate is better at what v3 adds, worse at the rest.** It names causes far better (cause
   accuracy 0.980 vs 0.857: production has no `cascade_unreachable`, `cascade_gray` or `router_*`
   class, so every such reading it catches gets a wrong cause name), catches more of the routers' own failures (router_down 0.910 vs 0.779,
   router_gray 0.177 vs 0.080) and of gray cascades (0.170 vs 0.105). It loses on the five local
   causes, most on `memory_leak` (0.822 vs 0.936; not investigated further).
3. **The classifier is not the weak point; the safety net's precision is.** Classifier alone, the
   candidate is better than production (F1 0.915 vs 0.898, precision 0.987 vs 0.915). Served
   precision for both is about 0.73 because the decision is classifier OR IsolationForest
   (Decision 10): every false positive beyond the classifier's comes from the IsolationForest.
   Tuning the safety net looks like a more promising lever than changing the training data.
4. **The same production model measures 0.80 F1 on v3 and 0.73 on v2.** Not a better model: v2's
   test labels include Decision 9's coin flip, which no model can score well (cascade recall 0.27).
   Labels that follow the data make the evaluation fairer.
5. **Hard failures are solved at group level.** Every one of the 453 hard-down routers in the test
   month is named, median 30 s after it fails, by both models and at every setting of the rule.
6. **Gray failures stay mostly invisible**, and no setting of the rule fixes that. At the shipped
   setting the router is named in 5% (production) or 10% (candidate) of gray incidents, with under
   1.5 false alarms per router-day. A longer window or a lower share buys detection only with false
   alarms: about 70% of gray incidents costs about 800 false alarms per router-day. That is the
   documented nature of gray failure (differential observability). The limit is the **feature
   set**: cpu, ram, network, disk and load do not carry what a gray failure leaves, retransmissions
   and latency. The concrete next step is a seventh feature, the TCP retransmission ratio that
   node_exporter already exposes (`node_netstat_Tcp_RetransSegs` over `node_netstat_Tcp_OutSegs`),
   emitted by the generator as well.

**History of this section.** A first study on 3-day fleets reported that v3 data trains a better
model (F1 0.7248 → 0.7629). That comparison was confounded (different test fleets, a tenth of
production's training data), and the same run showed production scoring higher on the v3 test
fleet. It was corrected in the README (commit `84f48ac`) and replaced by the full-size experiment
above.

**Detection side: the Fleet tab reasons like network monitoring.** Each router card is classified
from the live model verdicts by `api/fleet_rules.py` (tested in `tests/test_ui_fleet.py`; measured
above with production: all 453 hard-down routers named, gray failures only when strong, 0.7 false
alarms per router-day):

| Pattern on the card | Reading shown |
|---|---|
| most dependents stopped reporting (no report for 2 min) | router down or uplink lost; the machines are **unreachable, not failing** |
| router and ≥40% of its dependents anomalous | router incident; likely root cause the router |
| ≥40% of dependents degraded, router's own metrics normal | **suspected gray failure upstream**; likely root cause the router |
| router anomalous, dependents healthy | control-plane event, or a failure not yet propagated |

**Questions a reviewer will ask, and the answers** (counts from the training fleet's realism report)

- *"15 machines behind one router are anomalous and the router is normal: how?"* In v3 this only
  happens in the recovery tail (up to 90 s) after the router's incident (449 of 449 cases, 0
  unexplained), because machines take a little longer than the router to recover. In live data,
  the same picture with no recovery to explain it is exactly a gray failure, and the Fleet tab
  flags the router as the suspect. The shipped v2 data shows it for a different reason: its
  generator defect (Decision 9b).
- *"What if every machine behind a router is anomalous?"* In 96% of such moments (6,080 of 6,352
  router-steps) the router is hard down; the other 4% (272) are severe daytime gray failures. The
  router is anomalous in both. In a hard-down incident, monitoring sees every dependent go silent
  at once and reports it unreachable, with one alert on the router; v3 reproduces that (100% of
  dependents unreachable in every hard-down incident) and the Fleet tab says "unreachable, not
  failing".
- *"The router is anomalous and its machines are fine?"* Always during a control-plane event (2,728
  of 2,728 steps: management CPU busy, forwarding fine). In a gray failure, during the first minutes
  (2,839 steps) or when the loss is too mild for the current traffic (1,932). Never during a hard
  down (0 of 6,538).
- *"Why are some machines behind a failing router still green?"* In a gray failure the impact depends
  on the tier: batch jobs are not request-driven, so a few minutes of packet loss slows them without
  a material change. `scripts/fleet_status.py --data <a v3 fleet>` draws any moment of a fleet. In a
  hard-down incident there are no exceptions: every dependent is unreachable.
- *"Why do routers fail so often in your data?"* They do not in production (under 5% per year,
  Gill et al.). Rates are compressed so the models see enough incidents to learn from, as
  fault-injection benchmarks do; durations and propagation timing are kept realistic.
- *"Why not adopt the more realistic data?"* Because a model trained on it measured worse, and the
  guardrail exists to stop exactly that. Adopting v3 becomes worthwhile when its extra signal (the
  retransmission feature) gives the classifier something v2 cannot.

**Limitations.** Each machine depends on one router (single-homed), like Facebook's
one-switch-per-rack design (Meza et al., IMC 2018); with dual-homed servers, redundancy would mask
part of the impact (median traffic per redundancy group 93% vs 65% per link, Gill et al.). No
application-tier propagation (web → app → db), which was tried and reverted in Decision 6.
Unreachable rows keep the host-local agent's idle readings (as an agent with local buffering would
record them), flagged `reachable=0`. One seed per fleet and no confidence interval: small gaps
(the 0.006 precision difference) should not be over-read; the curve comparison is the stronger
evidence because it holds at every threshold up to 0.88 recall.

**Sources for section 9c** (every source the failure model, its parameters and this analysis draw on):

1. P. Gill, N. Jain, N. Nagappan, *Understanding Network Failures in Data Centers: Measurement, Analysis, and Implications*, ACM SIGCOMM 2011. Failure rates per device type, durations, traffic during failures, redundancy effectiveness. https://people.cs.umass.edu/~phillipa/papers/comm235-gill.pdf
2. P. Huang et al., *Gray Failure: The Achilles' Heel of Cloud-Scale Systems*, HotOS 2017. Differential observability, silent packet drops. https://www.microsoft.com/en-us/research/publication/gray-failure-achilles-heel-cloud-scale-systems/ (summary used: https://blog.acolyer.org/2017/06/15/gray-failure-the-achilles-heel-of-cloud-scale-systems/)
3. B. Arzani et al., *007: Democratically Finding the Cause of Packet Drops*, USENIX NSDI 2018. Silent drops invisible to SNMP; host TCP retransmissions as the signal. https://arxiv.org/pdf/1802.07222
4. M. Mathis, J. Semke, J. Mahdavi, T. Ott, *The Macroscopic Behavior of the TCP Congestion Avoidance Algorithm*, ACM SIGCOMM CCR 1997. TCP throughput ∝ 1/√p. https://courses.cs.duke.edu/fall24/compsci514/readings/mathis-tcpmodel-ccr97.pdf
5. J. Meza et al., *A Large Scale Study of Data Center Network Reliability*, ACM IMC 2018. Rack-switch incidents, single switch per rack, root-cause categories. https://conferences.sigcomm.org/imc/2018/papers/imc18-final179.pdf
6. Nagios, *Parent/child host relationships and network reachability* (UNREACHABLE vs DOWN, notification suppression). https://support.nagios.com/kb/print-904.html
7. Grafana Labs, *Handle connectivity errors in alerts* (`up == 0`, missing data). https://grafana.com/docs/grafana/v12.4/alerting/guides/connectivity-errors/
8. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.), *Site Reliability Engineering*, O'Reilly 2016, ch. 22 "Addressing Cascading Failures". https://sre.google/sre-book/addressing-cascading-failures/
9. B. Gregg, *Linux Load Averages: Solving the Mystery*, 2017. What the load average counts. https://www.brendangregg.com/blog/2017-08-08/linux-load-averages.html
10. L. Pham et al., *RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data*, 2024. Fault injection and root-cause labelling. https://arxiv.org/html/2412.17015v1
11. OneUptime, *Which Network Metrics Catch Host Problems? Drops, Errors, and Saturation*, 2026. `node_netstat_Tcp_RetransSegs` and the retransmission ratio. https://oneuptime.com/blog/post/2026-07-31-host-network-metrics-drops-errors-retransmits-saturation/markdown
12. Facebook Engineering, *More details about the October 4 outage*, 2021. Maintenance command, backbone down, DNS withdrawal (the pattern listed under future work). https://engineering.fb.com/2021/10/05/networking-traffic/outage-details/
13. Lucide icons (ISC licence; portions from Feather, MIT), the equipment pictograms in `scripts/fleet_status.py` and the Fleet tab. https://lucide.dev

**Adopting it later** changes every dataset, both classifiers, the IsolationForest, the selected
threshold, the guardrail baseline and every number in this README. `scripts/v3_experiment.sh`
already generates the four fleets and trains on them without touching production; adopting means
running the same training with production's `--models-dir`, after the guardrail passes, and
pointing the production agent and the panel sampler at the v3 files.

### 10. Why dual-model architecture (primary + safety net)

RandomForest is blind to cascades by construction (see previous). IsolationForest, being unsupervised, has no such blind spot -- it reacts to any statistical deviation regardless of label noise (0.262 cascade recall). Running both means the primary gets high accuracy on the 5 clean cause types AND cascades still get detected via the safety net. Two rejected alternatives:

- **Blind ensemble (flag if either fires)**: F1 = 0.663 in the RandomForest era, worse than RandomForest alone on F1. The shipped rule is this same OR, kept on purpose for recall, not for F1; the measured trade-off at the served threshold is in the table below.
- **Graph-rule cascade fix (flag all downstream when router flagged)**: F1 = 0.550. Blast radius: each router has ~22 downstream machines; one wrong router prediction = ~22 wrong flags. Cascade-rule precision on fired rows: 2.9%.

Both rejections are documented with real numbers, not hand-waved. This remains the shipped architecture today: the served decision is still `classifier OR IsolationForest` (`ml-model/decision.py`), unchanged in principle since this decision was made, though the classifier and the specific safety-net numbers have moved on since (see [L0 — ML Model](#l0--ml-model) and the [unsupervised-model comparison](#why-isolation-forest-updated--corrected-methodology) in L0).

**Measured on 19 October, at the served threshold (0.91), on the full 17,280,000-row test set** (`scripts/decision_components.py`, same model outputs for all three rows; the OR row reproduced that model's 0.7291 / 0.6713 / 0.7979; the 21 October retrain scores 0.7292):

| Decision | Precision | Recall | F1 | Cascade recall |
|---|---|---|---|---|
| XGBoost classifier alone | 0.8972 | 0.7382 | **0.8100** | 0.041 |
| IsolationForest alone | 0.6666 | 0.6610 | 0.6638 | 0.251 |
| **Served: classifier OR IsolationForest** | 0.6713 | **0.7979** | 0.7291 | 0.268 |

What this says, without softening it:

- The OR rule is **not** an F1 win. The classifier alone has the better F1 (0.81 against 0.7291) and far better precision. The OR rule buys **+6 points of recall** (0.7382 -> 0.7979) and pays 22.6 points of precision: the IsolationForest adds 356,428 false-positive readings that the classifier would not have raised.
- On the five learnable causes the safety net adds almost nothing (recall changes by less than half a point on every one, for example memory_leak 0.929 -> 0.933). Its real contribution is **cascade**, 0.041 -> 0.268, which the classifier is trained not to learn (label-noise decision above).
- The shipped rule therefore follows the project's stated priority in `config/model_policy.json` (recall first: `min_recall` 0.80, F-beta with beta 3), not F1. If false alarms were the priority, the data says to serve the classifier alone. This is a deliberate trade-off, not a free improvement, and the recall target is still narrowly missed (0.7979 against 0.80).
- All of it is measured on simulated data with generator labels; cascade labels are only 40% consistent by construction, so cascade recall is capped by the labels themselves.

### 11. Why XGBoost replaced RandomForest as primary (v3)

Full-scale offline comparison, same train/test data as v2:

- **RandomForest**: F1 = 0.731, Precision = 0.849, Recall = 0.641, 125MB, 340s train
- **XGBoost**: F1 = 0.718, Precision = 0.775, Recall = 0.670, 3.6MB, 74s train

Overall F1 favors RandomForest by 1.3 points, but XGBoost wins per-cause recall on **every single anomaly type**: cpu_spike, memory_leak (0.688 -> 0.736 -- the previously weakest category), network_flood, disk_saturation, silent_failure, cascade. In a telecom monitoring context, **missing a real anomaly costs more than an extra investigation** -- a missed memory leak leads to a crash, a false alarm costs 5 minutes of an engineer's time. Recall priority is the right operational choice. Live K8s validation confirmed the offline result: v3 XGBoost catches 49 more real anomalies over a 33,600-request test window than v2 RF, with 2.5x throughput and lower latency.

**Architectural bonus:** XGBoost's 3.6MB model fits directly in the Docker image, eliminating the entire MinIO-fetch-at-startup mechanism built for v2's 125MB RandomForest. Simpler deployment, faster startup, one less runtime dependency.

### 12. Why LightGBM was tested but not adopted

Same methodology as XGBoost: F1 = 0.716, Precision = 0.767, Recall = 0.671, per-cause recall within 0.001-0.002 of XGBoost on every category. Essentially identical performance, 3.0MB model (marginally smaller), 51s training (marginally faster). Kept as evidence because the negative result is genuinely useful: **it confirms the ceiling on this data with these features is a gradient-boosting-family ceiling, not an XGBoost-specific one**. Meaningfully passing F1 = 0.72 would require something structurally different (sequence-aware models, richer temporal features, or larger real-world data), not another gradient booster.

### 13. Why rolling/trend features WERE integrated as v4 (updated)

Per-machine rolling mean, rolling std, and delta features (10-reading window,
cpu/ram/load_avg) were first tested on a 5-day pilot (F1 0.708 -> 0.729) and
initially left out of production because they seemed to require a stateful
serving path. That concern was resolved and the features were fully
integrated as the v4 model. At full scale the gain was substantial: served
F1 (independent seed-123 test set, 17,280,000 rows, current threshold 0.91)
went from 0.6667 (v3) to 0.7292 (v4) -- see [L0 — ML Model](#l0--ml-model) for
the current, corrected numbers, which supersede the offline figures this
subsection originally quoted.

The stateful-serving problem was solved WITHOUT server-side state by having
the **client supply the recent history** in the /predict request (the pattern
Datadog/New Relic use). The API stays stateless: when `history` is present it
computes the 9 rolling features and uses the 15-feature v4 model; when absent
it falls back to the 6-feature v3 model. See the "Rolling features and
gradual-onset detection (v4)" section for the full design, and Decision #21
for the stateless-serving rationale. This entry is kept (rather than deleted)
to record that the original "not integrated" decision was later revisited and
reversed on stronger full-scale evidence.

### 14. Why SMD real-world validation matters

F1 = 0.269 on the Server Machine Dataset (28 real servers, public benchmark used by OmniAnomaly and other sequence-model papers). Much lower than synthetic F1, and this is the honest point: **published SMD results (e.g. OmniAnomaly, KDD 2019) use recurrent models over a sliding window and usually point-adjusted F1, a more lenient metric, so they are not comparable with this per-reading score** (an earlier uncited "0.40-0.55" range was removed). This project prioritizes training speed, interpretability, and simple CI/CD-integrated deployment over the marginal accuracy gains of a recurrent sequence model -- a documented tradeoff, not an oversight. The SMD result's purpose is not to win on the benchmark; it shows the per-machine, per-window z-score methodology transfers to real, independently-collected data at reduced accuracy (F1 0.269), so the synthetic-data scores must not be read as real-world accuracy. `ml-model/load_smd.py` is the adapter that makes this data loadable through the same pipeline; it remains in the repository and is not part of the production training path.

### 15. Why MLflow + MinIO run outside the Kubernetes cluster

Same reasoning as Jenkins, SonarQube, and the local Docker registry: **shared platform/tooling services supporting the development process are not the product being served to end users**. In a real organization, one MLflow server tracks experiments across many projects; it does not live inside a single project's own K8s namespace. Only the anomaly-api workload runs in K8s (namespace `ml-serving`), matching how production would separate stateful tooling from stateless application deployments. As of the most recent infrastructure pass, MLflow itself now runs as a supervised systemd service rather than a manually-started process -- see [L6 — Ansible](#l6--ansible-infrastructure-as-code) -- closing a real gap where it had, for a time, not been running at all.

### 16. Why the CI/CD pipeline uses fail-fast pytest before SonarQube

Stage 1b (pytest) runs the full test suite before any downstream stage. If a commit breaks the tests, the pipeline stops immediately, before wasting 5-10 minutes on SonarQube scanning, Docker build, Trivy CVE scan, and registry push. The Quality Gate itself, however, changed since this decision was first written: it originally did **not** abort the pipeline (`abortPipeline: false`) on the reasoning that code-quality issues are advisory. That reasoning was revisited in the most recent CI/CD pass and reversed -- `abortPipeline: true` is now set, proven live by two real pipeline runs that correctly stopped on a genuine Quality Gate failure before this change was validated. See [L3 — CI/CD](#l3--cicd) for the full current pipeline and why the change was made.

### 17. Why the custom SonarQube Quality Gate

The default Sonar Way gate demands 80% coverage and less than 3% duplication. Neither threshold fits a research-heavy ML repository where most of `ml-model/` is one-shot experiment scripts (not library code intended for unit testing) and 20%+ duplication is intentional (near-identical variant scripts for A/B comparison). Custom gate `devsecmlops-pfe-research`: coverage >= 5% (project has 6.7%, passes honestly), duplication <= 25% (has 21%, passes honestly), 0 New Issues (strict), Security Hotspots strict. **This is engineering judgment, not gaming the metric** -- adjusting thresholds to reflect what "good" actually means for this project, while keeping bug/security requirements strict.

### 18. Why models are baked into the Docker image (v1, v3, v4), not fetched

Image tag = exact model version. Immutable, reproducible, no runtime dependency on external artifact storage. v2's 125MB RandomForest violated this rule -- too large for git and too large to bake into the image comfortably -- so v2 needed the MinIO-fetch entrypoint as a workaround. The switch to v3 XGBoost (3.6MB) restored the baked-in pattern and eliminated the MinIO dependency for the primary model; v4's artifacts follow the same small-and-baked-in pattern.

### 19. Why v2 code paths were kept alive after v3 switch

Setting `MODEL_NAME=telecom_v2` in the K8s deployment env still fully works. The v2 loading block, MinIO fetch entrypoint, and RandomForest artifact all remain functional. Reason: **additive changes are safer than destructive ones**, and defense-day A/B comparison ("here's v2 running with RandomForest, here's v3/v4 running with XGBoost, watch them differ on the same input") is a real, tangible demonstration that would be lost if v2 were deleted.

### 20. Why the decision threshold is selected by the pipeline, not fixed (revised twice)

v3/v4 flag an anomaly when P(normal) falls below a threshold. This decision
was originally written to justify **0.85**, and that original choice did
have real evidence behind it -- a documented sweep at 0.50/0.85/0.95 on the
XGBoost classifier alone, with 0.85 chosen for preserving usable alert
precision. What that sweep never did is account for the served decision as
a whole: it measured the classifier in isolation, never re-validated after
the IsolationForest safety net's OR-gate was added to the served path. The
reasoning about *priorities* -- a missed incident costing more than a false
alarm -- was correct then and is kept now; what was missing was measuring
the actual served decision, not the classifier alone. The
threshold is now selected by `ml-model/select_threshold.py`, which sweeps
0.05-0.95 on a validation fleet the classifiers and the search itself never
train or tune on (seed 7, separate from both the seed-42 training set and the
seed-123 test set), maximizing **F2** -- recall weighted twice precision,
which is exactly the "a miss costs more than a false alarm" priority this
decision always argued for, now backed by a real sweep instead of a single
chosen number. The result, 0.60, is recorded with its full evidence curve in
`models/manifest.json`, and a CI gate (`scripts/verify_model_manifest.py`)
enforced that every deployment file agreed with it. See
[Choosing the decision threshold](#choosing-the-decision-threshold) in L0
for the full table.

**Revised again on 19 October 2026.** A fixed number is the wrong shape for a model that is retrained: the best threshold is a property of one trained model and of its training data (training on all rows instead of a sample moved it from 0.60 to 0.91). The pipeline now selects it per model on the validation fleet by a rule kept in `config/model_policy.json`, and the project's stated priority, catching anomalies over avoiding false alarms, is written there as a recall target (0.80), not buried in code. The result is stored in `models/manifest.json` and read by the API at start-up, so no deployment file holds a copy and nobody edits several files. `PREDICT_THRESHOLD` remains only as an explicit override, and `/health` says where the value came from. A target of 0.80 is what the data supports: a higher target is not reachable by any threshold, because the IsolationForest and the classifier together top out at about 0.81 to 0.82 recall.

### 21. Why the client supplies rolling history (stateless v4 serving)

v4 needs the last 10 readings per machine to compute rolling features. Three
ways to get them: (a) keep per-machine buffers inside the API pods -- but with
2+ replicas sharing no state, buffers would be inconsistent and would need
Redis or sticky routing; (b) have the API query Prometheus on every /predict
-- couples the API to Prometheus and doubles latency; (c) have the CLIENT send
the history in the request. We chose (c): it matches how real monitoring
systems work (Datadog/New Relic clients compute and ship features), keeps the
API stateless and horizontally scalable, and makes v4 backward compatible
(history absent -> v3 fallback). The serving-side rolling computation is
verified byte-identical to the training-time function.

### 22. Why the feedback DB was rebuilt from SQLite to PostgreSQL

The feedback loop was first built on SQLite (single file on a PVC). That was
rebuilt to PostgreSQL (StatefulSet + headless Service + Secret + PVC) for
genuine production-readiness: multiple API replicas can write concurrently
without file-lock contention (verified: 20 parallel writes in 0.45s), data
survives a hard pod kill (verified: StatefulSet recreates, PVC persists), and
it is a standard client-server DB rather than a shared-file compromise. A real
bug surfaced during the rebuild and is worth recording: the column originally
named `window` is a PostgreSQL reserved keyword (SQLite accepted it), so it was
renamed to `time_window`; and the K8s env ordering matters -- POSTGRES_PASSWORD
must be declared before DATABASE_URL for `$(VAR)` substitution to resolve.
The most recent security pass rotated this database's actual password live
(the previous one had been committed to git) and hardened the connection
handling further -- see [Security](#security-posture) and
[L4 — Kubernetes](#l4--kubernetes).

### 23. Why feedback logging is best-effort, not fail-loud (graceful degradation)

Model serving (/health, /predict) is the critical function; the feedback log is
secondary. Originally the API called the DB on startup and on every /predict,
so an unreachable database crashed the whole app -- which also broke the CI
smoke test (the container runs standalone with no PostgreSQL). Fixed with a
FEEDBACK_DB_AVAILABLE flag and a _safe_insert_prediction wrapper: if the DB is
down, the API logs a warning and keeps serving predictions (prediction_id comes
back null, logging is skipped). More robust in production AND makes the smoke
test pass -- a monitoring API should never stop detecting anomalies because its
feedback log is offline. Hardened further in the most recent pass: the
connection now retries automatically every 30 seconds rather than requiring a
pod restart, and the feedback endpoints (which genuinely need the database)
now return a proper `503` instead of an uncaught `500` when it is down.

### 24. Why the retrain guardrail is strict, and what 500 rows proved (updated)

The retrain pipeline only promotes a new model if aggregate F1 does not drop
AND per-cause recall drops by no more than a small, fixed margin on any
category. The original guardrail (`scripts/retrain_from_feedback.py`, since
superseded) used a 5-point recall margin; the current one
(`ml-model/train_production.py --feedback`, the single canonical training
path — see [L0 — ML Model](#l0--ml-model)) is stricter: **zero** aggregate F1
regression allowed at all (`--max-f1-drop 0.0`) and a 2-point recall margin
(`--max-recall-drop 0.02`). The finding this decision records is unchanged and
still real: tested end-to-end with 500 simulated operator verdicts, the
retrained model scored F1 0.709 vs the current 0.718 (offline numbers from
that test, predating the threshold and evaluation corrections elsewhere in
this document), and the guardrail correctly **REJECTED** it. This exposed a
real, honest finding: 500 feedback rows against millions of training rows is
a tiny fraction of the signal -- too little to move the model regardless of
sample-weight. Meaningful improvement needs thousands of real verdicts
accumulated over weeks/months; the pipeline is production-ready and the
guardrail protects production in the meantime. (A z-score evaluation bug was
also found and fixed while verifying this: a quick eval script that skipped
add_window_column + apply_zscore mis-scored the baseline; the canonical F1
was confirmed once the correct preprocess path was used -- the same class of
"evaluate through the real served path, not a shortcut" discipline that
`ml-model/decision.py` now enforces structurally for every reported number in
this project.)

---

## Testing

**179 tests** in 18 files (up from 15), `pytest`, in `tests/`:

- `test_api.py` (38 tests) — every endpoint, input validation (NaN/inf
  rejection, physical-range rejection (cpu=150 is a 422), the opt-in feedback token, machine length, metrics count, history shape), v3/v4
  auto-selection, Prometheus metrics, DB-unavailable behavior (503, not
  500), ops-UI auth (required, wrong password rejected, absent when
  disabled), and artifact-integrity rejection of a tampered model file.
- `test_decision.py` (5 tests) — the scalar and vectorized forms of the served
  decision rule produce identical results, at multiple thresholds, with and
  without per-class overrides.
- `test_preprocess.py` (8 tests) — baseline construction, the fallback chain, z-score
  correctness, the standard-deviation floor that prevents division by zero.
- `test_train_production.py` (22 tests) — the full training pipeline and the model gate: promotion with
  correct lineage, the CI gate accepting a genuinely promoted model and
  rejecting a tampered or low-F1 one, retraining determinism, a rejected
  candidate leaving production byte-for-byte untouched, and feedback rows
  with an unrecognized cause skipped rather than crashing. The threshold lifecycle has its own
  tests: the gate accepts a selection made for these artifacts and rejects a missing selection,
  a selection made for other artifacts, an evaluation at another threshold, and a validation
  fleet that is the test set; promotion refuses a missing, test, or train file as the validation
  fleet; and a promoted threshold is selected for the candidate that carries it. The standalone selection tool refuses a non-independent validation fleet, and refuses `--record` for any directory other than `models/`.
- `test_threshold_policy.py` (21 tests) — the policy file is validated; the threshold rules (F-beta, minimum recall, recall target unreachable); training uses every normal row when asked; command-line defaults come from the policy; the guardrail also checks recall; the API takes its threshold from the manifest, an environment override, or a fallback, in that order, and says which; no file in the repository hard-codes a threshold; and the model gate flags one that does.
- `test_training_console.py` (8 tests) — the dataset whitelist (rejects path
  traversal in every form tried), that sampling genuinely spreads across
  the whole file, and that a candidate is compared against production on
  the same rows.
- `test_ui_cluster.py` (15) — the continuous-training panel routes: cluster proxies, read-only tab
  data, and that every `/ui/` route requires the ops credentials.
- `test_ui_fix.py` (13) — every layer on the Status tab has a fix; Stop/Fix go through systemd only (a `pkill` is undone
  by `Restart=always`; a Fix must not start a second copy).
- `test_ui_fleet.py` (10) — the Fleet tab: topology, colours from the pods' verdicts, staleness, and
  every state of the group rule.
- `test_review_details.py` (12) — the Review tab's details and real-label column: the label store,
  the review payload, and the production agent recording labels.
- `test_failure_model_v3.py` (8) — the v3 generator: one incident drives the router and its
  dependents, gray / down / control-plane behave as documented, labels follow impact, and the
  realism report finds nothing unexplained.
- `test_rack_tools.py` (3) — the vectorised group rule equals the API's rule on 5,000 random cases;
  the curve comparison and the window sweep run end to end.
- `test_compare_v2_v3.py` (2) — the v2/v3 comparison reads the manifest and the run report.
- `test_cause_metric.py` (3) — one `api_anomaly_cause_p` series per currently anomalous machine.
- `test_prediction_logging.py` (2) — a prediction is logged with the history it carried, so
  feedback on a v4 prediction can become a v4 training example.
- `test_export_feedback.py` (4) — the feedback exporter with `kubectl` stubbed: relative and
  outside output folders, empty table, `kubectl` failure.
- `test_sampler_source.py` (2) — the panel's sampler never reads the test fleet.
- `test_ci_config.py` (3) — `scripts/ci_config.py` against a fake Jenkins and SonarQube.

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

Wired into Jenkins as stage 1b (fail-fast) — a broken commit stops the
pipeline before any downstream stage runs, described fully in
[L3 — CI/CD](#l3--cicd).

**Live K8s validation:** `scripts/live_k8s_validation.py` — described in
full in [Current production](#current-production-the-numbers-that-supersede-everything-above) above — replaces the earlier
`live_k8s_demo_test.py`/`live_k8s_demo_test_v4.py` scripts, which measured
against the training dataset rather than the independent one.

---

## Reproducing locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Generate the synthetic fleet
python ml-model/generate_telecom_fleet.py --machines 200 --days 30 --anomaly-ratio 0.05

# Generate the independent validation fleet (its own seed; used only to select the threshold)
python ml-model/generate_telecom_fleet.py --machines 200 --days 14 --seed 7 \
  --output data/telecom_fleet_v2_val.csv

# Generate the operator-stream fleet the control panel samples from (its own seed; never trained on until judged)
python ml-model/generate_telecom_fleet.py --machines 200 --days 3 --seed 2026 \
  --output data/telecom_fleet_v2_operator.csv

# Evaluate a candidate WITHOUT promoting: trains it and compares it with production on the test set
python ml-model/train_production.py --report build/report.json

# Promote: first selects the candidate's OWN threshold on the validation fleet, then evaluates the
# candidate at that threshold and production at its own, applies the guardrail, and only then promotes
python ml-model/train_production.py --promote --report build/report.json

# Re-select the threshold for the model currently in production, then re-record its evaluation
python ml-model/select_threshold.py --record
python ml-model/train_production.py --evaluate-production

# Benchmark the unsupervised safety net against 5 alternatives
python ml-model/benchmark.py --data data/telecom_fleet_v2_test.csv --max-rows 0

# Run the z-score demo (defense script)
python ml-model/zscore_demo.py

# Serve directly
MODEL_NAME=telecom_v3 uvicorn api.app:app --host 0.0.0.0 --port 8000

# Or containerized
docker build --build-arg APT_REFRESH=$(date +%s) -t devsecmlops-api:$(cat VERSION) .
docker run -p 8000:8000 devsecmlops-api:$(cat VERSION)

# Test endpoints
curl localhost:8000/health
curl -X POST localhost:8000/predict -H "Content-Type: application/json" \
  -d '{"machine":"web-01","hour":14,"metrics":{"cpu":30,"ram":50,"network":80,"disk_io":25,"disk_usage":40,"load_avg":1.2}}'
curl -X POST localhost:8000/root-cause -H "Content-Type: application/json" \
  -d '{"anomalies":{"router-01":0.55,"web-01":0.62}}'

# Full-stack monitoring
python monitoring/production_agent.py --target local &
python monitoring/k8s_exporter.py &
# Prometheus + Grafana: monitoring/grafana/dashboards/devsecmlops-fleet.json
```

---

## Documentation and evidence

| Path | What it is |
|---|---|
| `docs/TROUBLESHOOTING.md` | A runbook written from real incidents: Docker networking staleness, Minikube API timeouts, a rejected retrain (the guardrail working as intended), MLflow service checks, browser-vs-VM connectivity, Jenkins pytest/venv failures. Corrected in this pass: it referenced the deleted `retrain_from_feedback.py` and told operators to hand-launch MLflow with hardcoded MinIO credentials. |
| `docs/MINIKUBE_RELIABILITY.md` | Why the cluster was unreachable after a reboot (Jenkins taking the node's fixed address, no autostart, missing `--force`), the permanent fix, and how to verify it |
| `docs/machine_roster.txt` | The 200 simulated machines and their roles. |
| `scripts/feedback_demo.py` | A one-page UI that walks the feedback loop: send readings to the live API, judge them, retrain in a memory-capped sandbox (`/tmp/feedback_demo`, tiny generated fleets) with the verdicts, and prove by hashing that the production models were not touched. Standard library only; run it from the repository root. |
| `screenshots/` | 12 PNGs captured at real milestones: the six-model benchmark, the final training run, a three-way model experiment, the z-score demo, the data generator, `/health`, a "3am problem" scenario, a disk-usage anomaly, the baseline fallback chain, Docker images, a container health check, a registry push. |

---

## Known limitations and future work

- **The served model changed on 13, 19 and 21 October; the live validation was last run on 19 October.** On 21 October build 9 retrained with 315 judged feedback rows (v4 F1 0.7292, threshold 0.91 again). On 19 October build 8 trained on all 17.28M rows and re-selected the threshold (0.91), v4 F1 0.7291. Until 13 October the served models were trained in July by earlier scripts and adopted with hashes (manifest `git_commit: null`); on 13 October the pipeline-trained candidate replaced them (MLflow run `f622a7a9…`, v4 F1 0.7278). `scripts/live_k8s_validation.py` ran on 19 October against that day's model (16,600 requests, 0 errors, see Current production) and was not repeated for the 21 October retrain. The live-versus-offline comparison made earlier in this project (precision 0.676, recall 0.788, F1 0.728 over 39,789 matched live predictions) was measured on the July model and was not repeated. No deployment file carries the threshold: the API reads it from the manifest, so a changed threshold needs no file sync.

- **Disk headroom is a CI dependency.** Every Jenkins build leaves a ~2 GB image on the host (the registry keeps its own copy, so local images are safe to prune), and SonarQube's Elasticsearch switches every index to read-only when the disk passes 95% (observed live, and again at 97% before a cleanup). Unpruned, a day of builds is enough to reach it. Routine: `docker image prune -a -f --filter "until=24h"` after heavy CI days and a `df -h /` before a demo. The registry volume (about 6 GB, every tag since `2.3.0`) is the one store that only grows; trimming it needs registry garbage collection, which means recreating its container with deletes enabled and its network preserved.
- **Dependencies are pinned at the top level, not locked.** `requirements-api.txt`, `boto3` and `msgpack` are pinned exactly, but their transitive dependencies are only bounded (`>=`), so two builds hours apart can install different versions (observed: `python-dotenv` 1.2.3 → 1.2.4 and `uvloop` 0.22.1 → 0.23.0 between builds `b93` and `b94`; the image scan reports 0 vulnerabilities in both). A lockfile generated from the image's Python environment would close it; it needs its own verification cycle, so it is deliberately left for after the defense.
- **Operator feedback carries a verdict, not a cause.** `POST /feedback/{id}` takes `verdict` and `notes`, so a true positive that only the IsolationForest flagged (the classifier named no cause) has no label for the classifier and is skipped by retraining. In the sandbox demo run, 24 judged rows gave 21 used and 3 skipped, and F1 moved from 0.7152 to 0.7156: 21 rows are about 0.06% of the training rows, so the loop's mechanics are shown, not an improvement.
- **The NetworkPolicy is written but not enforced.** Pods are hardened individually (non-root, read-only filesystem, no capabilities, seccomp). `kubernetes/networkpolicy.yaml` lets only the API pods reach PostgreSQL, but Minikube's default network plugin does not enforce NetworkPolicy, so it is not applied on the demo cluster and not claimed. Enforcing it needs a CNI that does (e.g. Calico) and the check in `docs/INFRASTRUCTURE_SETUP.md` proving traffic is actually blocked.

- **`cascade` recall (0.27) is genuinely the weakest result in this
  project**, stated honestly rather than hidden — the generator's own
  cascade labeling is inconsistent row to row, which is why it is trained
  as `normal` and relies on the IsolationForest for coverage instead. See
  [Engineering decision 9](#9-why-cascade-is-folded-into-normal-during-training).
- **Gray failures are mostly invisible, even with realistic data** ([Decision 9c](#9c-failure-model-v3--router-incidents-modelled-the-way-networks-actually-fail)).
  On the full-size v3 fleets, every hard-down router is named within about 30 s, but the router
  is named in only 5% of gray-failure incidents (10% with a v3-trained model) at under 1.5 false
  alarms per router-day; catching about 70% costs about 800. The six features do not carry the
  signal a gray failure leaves (retransmissions, latency). A model trained on v3 data was rejected
  by the guardrail, so production keeps the v2-trained model.
- **The safety net costs most of the precision.** On the v3 test fleet the classifier alone reaches
  precision 0.91 (production) to 0.99 (v3 candidate); the served decision, classifier OR
  IsolationForest, is about 0.73 for both. Tuning the safety net is the more promising lever.
- **v4's rolling features cover 3 of 6 metrics** (`cpu`, `ram`, `load_avg`)
  — the ones where a *trend* carries more signal than a snapshot. Extending
  to all six is real, larger work (retraining v4 from scratch, re-selecting
  the threshold, re-evaluating), not attempted in this pass.
- **`Jenkinsfile.model` has run in both scopes and has promoted three times.** `smoke` (tiny generated fleets and a scratch copy of the production artifacts, production proven untouched by hash) and `full` (the real datasets, logged to MLflow and MinIO; see L5). On 13 October build 7 ran `SCOPE=full` with `PROMOTE=true`: guardrail PASS, commit `18042f37` pushed to `main` (push access of the token is verified), and the application pipeline started; its build #100 failed and was re-run by hand as #101 (green, deployed). The automatic deploy-after-promote step was proven on 19 October: build 8 promoted, and the application pipeline it started (#103) went green and deployed `2.20.3-b103`. On 21 October build 9 ran the whole loop with operator feedback (315 judged rows), and the build it started (#111) went green and deployed `2.20.3-b111`. The promotion path is also covered by tests and the CI model gate.
- **Headline scores are on synthetic data.** The F1, precision and recall quoted for the deployed model (0.7292 / 0.671 / 0.798) are measured on a generated fleet whose anomalies and labels come from the same generator, and the operator verdicts in the feedback loop are simulated from those labels. The only real-data check is SMD (F1 0.269), so these numbers must not be read as real-world detection accuracy.
- **Unseen machines are the real weak spot (measured, `scripts/new_site_eval.py`).** On a fresh 7-day fleet (seed 777, 4,032,000 rows) at the served threshold 0.91: with the same machine names F1 is 0.730; with renamed machines whose type is known, 0.758 (the per-type baseline is enough); with renamed machines and **no type**, which is what `/predict` sees for a machine it has never heard of, **F1 drops to 0.386** (precision 0.289, recall 0.582) because the global baseline fits no machine type; with an added +-15% per-machine level shift, 0.352. The data are still simulated, but the mechanism is real: a new machine must be given a type, or have its own baseline, before its alerts can be trusted. The request has no `machine_type` field yet.
- **The "held-out" files are not a different site.** The training, validation and test fleets use different seeds, so they share no rows, but they describe the same simulated fleet (same 200 machine names, same calendar, same generator) with independent noise. Scores on a genuinely new infrastructure would be lower, as the SMD result suggests. The threshold is selected on the validation fleet, but the promotion guardrail compares models on the test set, which is also the set the headline numbers come from, so that set is not a fully untouched holdout.
- **Recall is per reading, not per incident.** 0.7979 means 79.8% of anomalous 30-second readings are flagged. An incident lasts many readings, so an operator is told about most incidents sooner than this figure suggests, but the project does not yet measure time-to-detect or per-incident recall.
- **Access control is minimal outside the Operations UI.** `/predict` rejects out-of-range readings (percentages outside 0-100, negative network or load average) with a 422. `POST /feedback/{id}` is open by default, because the demo needs it; setting `FEEDBACK_TOKEN` on the API (and the same value for the callers) makes it require an `X-Feedback-Token` header. Verdicts feed retraining, so on a shared network that token should be on.
- **The Ansible setup has been validated on the existing VM, not on a blank machine.** `--syntax-check`, `--check` and idempotent re-runs pass there; a first run on a clean Ubuntu host has not been done (see `docs/INFRASTRUCTURE_SETUP.md` for the order that works). The Jenkins and SonarQube settings live in Docker volumes and are configured by hand.
- **Recall-first has a ceiling, and the full-data retrain gained little.** With a recall target of 0.80 the served recall went from 0.7941 to 0.7979 and F1 from 0.7278 to 0.7291 (single seed, within noise): about one anomalous reading in five is still missed, mostly `cascade` (0.268, label noise by design), and the IsolationForest carries most of the recall, so the threshold moves it little. Training on all rows gave no measurable v4 gain, nor did the 21 October retrain with feedback (+0.0001); the v3 gain (0.6550 to 0.6664) is confounded with the changed threshold. Recall is per reading, not per incident, and uses the generator's labels.
- **Sequence-aware detection remains future work.** The current models
  evaluate the current reading plus a short rolling window, not a full
  learned sequence model. State-of-the-art results on SMD (OmniAnomaly,
  Su et al., KDD 2019 — a GRU-VAE architecture) use a genuinely recurrent
  model over a sliding window and report higher F1 on that specific
  benchmark. This project prioritizes training speed, interpretability,
  and CI/CD-integrated deployment over that architecture's accuracy gain —
  a documented tradeoff, not an oversight.
- **A richer, mesh-topology dependency graph** — the current graph models
  only the network layer (router → machine, star topology, no redundancy).
  A validated service-call layer was tested and found to degrade detection
  (see [Cascading failures and root cause analysis](#cascading-failures-and-root-cause-analysis));
  a better approach would decouple the correlation injection from baseline
  computation, using the service graph only for root-cause ranking, never
  for anomaly injection.
- **A TCP retransmission feature** — the next step from Decision 9c: the retransmission ratio
  node_exporter already exposes (`node_netstat_Tcp_RetransSegs` over `node_netstat_Tcp_OutSegs`),
  emitted by the generator too, so gray failures leave a signal the model can learn. Not built.
- **Alertmanager integration** — wiring real Slack/email notifications when
  `/root-cause` identifies a `likely_root_cause`, closing the loop from
  detection to human notification. Not built.
- **No further validation against real, external infrastructure telemetry**
  beyond the existing SMD comparison. The Server Machine Dataset adapter
  (`ml-model/load_smd.py`) supports this; a next step would be a similarly
  honest comparison using the corrected benchmark methodology described in
  [L0 — ML Model](#l0--ml-model).

---

*ESPRIT × Tunisie Telecom — internship PFE, developed by Amine Shil.*
