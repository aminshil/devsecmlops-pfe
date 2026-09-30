# DevSecMLOps Platform

**Conception et mise en œuvre d'une plateforme DevSecMLOps Cloud-Native**
PFE ESPRIT × Tunisie Telecom — 2025–2026 — Amine Shil

A self-hosted, cloud-native platform that detects anomalies on IT infrastructure (CPU, RAM, network, disk, load) using machine learning, delivered through a fully automated, security-gated CI/CD pipeline. Includes dependency-graph-based root cause analysis to distinguish a true root cause from downstream cascading victims when multiple machines alert at once.

This document is organized **layer by layer (L0–L6)**. Every layer section follows the same shape: what it does, what was tested (including what was tried and rejected, with real numbers), real bugs found and fixed, and the files that make it up. Nothing here is asserted without a command, a log, or a test result behind it — where a number or a fix is described, the evidence is named alongside it.

---

## Contents

1. [Executive summary](#executive-summary)
2. [Architecture](#architecture)
3. [Quick start](#quick-start)
4. [L0 — ML Model](#l0--ml-model)
5. [L1 — Serving API](#l1--serving-api)
6. [Security posture](#security-posture-new)
7. [L2 — Container Image](#l2--container-image)
8. [L3 — CI/CD](#l3--cicd)
9. [L4 — Kubernetes](#l4--kubernetes)
10. [L5 — Observability and MLOps](#l5--observability-and-mlops)
11. [L6 — Ansible (Infrastructure as Code)](#l6--ansible-infrastructure-as-code)
12. [Engineering decisions and rationale](#engineering-decisions-and-rationale)
13. [Testing](#testing)
14. [Reproducing locally](#reproducing-locally)
15. [Documentation and evidence](#documentation-and-evidence)
16. [Known limitations and future work](#known-limitations-and-future-work)

---

## Executive summary

A production-oriented anomaly detection platform for a simulated 200-machine telecom fleet, built end-to-end across all seven layers (L0–L6), each independently verified live — not just built and assumed working.

**Current, served numbers** (independent test set, 17,280,000 rows, threshold 0.60 — selected by method, not assumption; see [L0](#l0--ml-model)):

| | F1 | Precision | Recall |
|---|---|---|---|
| v4 (production, with history) | **0.7246** | 0.6642 | 0.7970 |
| v3 (fallback, no history) | 0.6496 | 0.5721 | 0.7513 |

Live-validated against the real, running Kubernetes cluster: 2,200 real requests, 0 errors, F1 0.7267.

**Every failure the CI/CD pipeline surfaced was diagnosed, fixed, and re-run until it passed** — two SonarQube Quality Gate blocks (proving `abortPipeline: true` genuinely stops the build), a Trivy vulnerability-database download timeout, a DNS failure between Jenkins and SonarQube caused by a real gap in the Ansible container definitions, and a SonarQube server-side failure caused by the VM's disk reaching 95%. The most recent run (build `2.20.3-b87`) passed every stage, with zero image vulnerabilities and a post-deploy smoke test through the live pod. See [L3](#l3--cicd) and [L6](#l6--ansible-infrastructure-as-code).

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
 L5  Observability             Prometheus, Grafana, MLflow, real production traffic
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
                     Grafana (30 panels, provisioned from the repo)
```

Everything runs on a single self-hosted VM for the PFE demo. The same Docker images deploy unmodified to a multi-node production cluster — only configuration changes, not code. Ansible brings the whole platform up from a bare provisioned VM in either profile (demo or production) — see [L6](#l6--ansible-infrastructure-as-code).

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

#### How the model works, in two phases

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

---
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

---

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
> current production number — the current one is 0.7246 served (v4,
> threshold 0.60), stated in [Executive summary](#executive-summary) and
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
oversight. The SMD result's purpose is not to win on the leaderboard; it
confirms the same methodology generalizes to real, independently-collected
data and isn't an artifact of the synthetic generator. `ml-model/load_smd.py`
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
> seed-123 test set, threshold 0.60) is **0.6496**, stated in
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

> **This F1=0.816 figure is the classifier alone, at threshold 0.5 — not the served decision, and not at the threshold now deployed.** It is real and it is kept here because it is genuinely informative about the classifier's own strength. The number this project now reports as "production" is the *served* decision (classifier OR IsolationForest) at the *measured* threshold (0.60): **F1 = 0.7246**. See [Executive summary](#executive-summary) and [Current production](#current-production-the-numbers-that-supersede-everything-above) for the full, current, corrected numbers and the reasoning behind every difference from this section.

##### Live K8s results at the time (33,600-request two-week demo, threshold 0.85)

| Config | F1 | Precision | Recall | Cause acc |
|---|---|---|---|---|
| v3 @ 0.85 | 0.576 | 0.452 | 0.793 | 90.0% |
| v4 @ 0.85 | 0.690 | 0.597 | 0.817 | 91.9% |

v4 beat v3 on every live metric at the time, zero errors on 33,600 requests. memory_leak recall live: 77.9% -> 95.4%.

> **The 0.85 threshold used here was later found to have been chosen without
> a documented search** — see [Engineering decision 20](#20-why-the-decision-threshold-is-060-selected-by-method-revised).
> A proper F2-maximizing sweep on an independent validation fleet selected
> **0.60** instead. A fresh live validation at the corrected threshold, against
> the real deployed cluster, is in [Current production](#current-production-the-numbers-that-supersede-everything-above):
> F1 = 0.7267 on 2,200 real requests, 0 errors, every one served by v4.

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
sweep described in [Engineering decision 20](#20-why-the-decision-threshold-is-060-selected-by-method-revised) —
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
   — see [Engineering decision 20](#20-why-the-decision-threshold-is-060-selected-by-method-revised)),
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
[Choosing the decision threshold](#choosing-the-decision-threshold-new)
below, which is the methodology now used, precisely because of what this
experiment found.

A further, more recent live-serving validation — against the actual
deployed Kubernetes cluster, at the corrected threshold, on the independent
test set — is in [Current production](#current-production-the-numbers-that-supersede-everything-above): 2,200 real
requests, 0 errors, F1 0.7267.

---

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

#### Choosing the decision threshold (new)

v3/v4 flag an anomaly when `P(normal)` falls below a threshold. As explained
in [Engineering decision 20](#20-why-the-decision-threshold-is-060-selected-by-method-revised),
this project's threshold was, until this pass, **0.85 — chosen by
operational reasoning but without a documented search**. It has been
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
own default all agree with the one the evaluation was measured at.

#### Production numbers (independent test set, 17,280,000 rows, threshold 0.60)

| Served path | Precision | Recall | F1 | Classifier-only F1 |
|---|---|---|---|---|
| **v4 (production)** | 0.6642 | 0.7970 | **0.7246** | 0.8080 |
| v3 (fallback, no history) | 0.5721 | 0.7513 | 0.6496 | 0.6945 |
| v4 @ threshold 0.5, for reference | 0.6744 | 0.7928 | 0.7288 | 0.8153 |

Per-cause recall (v4, served decision, threshold 0.60): `cpu_spike` 0.9930,
`disk_saturation` 0.9981, `memory_leak` 0.9650, `network_flood` 0.9967,
`silent_failure` 0.9907, `cascade` 0.2848 (see
[Engineering decision 9](#9-why-cascade-is-folded-into-normal-during-training)
for why cascade remains the one weak point — the generator's own cascade
labeling is inconsistent between rows, unchanged since that decision was
first made). Cause accuracy on every true positive: 0.9985.

**On the number `0.816`, and on `0.85`, which circulated in earlier versions
of this document:** `0.816` is the classifier's own F1 at threshold 0.5, in
isolation — real, but never the served decision at any threshold. `0.85` was
never the product of the search above; it was this project's original,
unmeasured default. `0.7246` at threshold `0.60` is what a request to this
deployment actually returns today.

#### Live validation against the real, running cluster

`scripts/live_k8s_validation.py` sends real requests through the NodePort to
the real deployment, sampling every machine every 4 hours across the
independent test set, each with its own real preceding 10 readings as
history, reporting which model actually answered rather than assuming:

```json
{
  "requests": 2200, "errors": 0,
  "served_by_model": {"telecom_v4_rolling": 2200},
  "f1": 0.7267, "precision": 0.6229, "recall": 0.872,
  "cause_accuracy_on_detected": 1.0
}
```

Every one of 2,200 real requests was answered correctly by v4, with 0
errors. This supersedes the earlier 33,600-request live validation reported
in the "Rolling features (v4)" section above, which used the unmeasured 0.85
threshold and, in an even earlier iteration of this project, was measured
against data that overlapped the training set — that overlap has since been
corrected (this script now reads only the seed-123 independent file, with
its SHA-256 recorded in the output specifically so the claim is checkable),
and this run is the current, trustworthy record.

#### The unsupervised-model comparison (updated)

Already covered in full in [Why Isolation Forest](#why-isolation-forest-updated--corrected-methodology) above, with the
corrected benchmark methodology and the current numbers.

#### Model integrity

`models/manifest.json` (schema 2) records, for the current production
model: every artifact's SHA-256, the datasets it was trained and evaluated
on (also fingerprinted), the selected threshold and its evidence, and the
full evaluation. Both the API (before unpickling any model file —
unpickling executes code) and CI verify these hashes match before doing
anything else. A model file that differs from what the manifest promoted is
refused, not loaded.

---

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
rather than hardcoded — see [Security posture](#security-posture-new) for the
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
   test set, applies the guardrail, and only if it passes, writes the new
   artifacts and records full lineage (including the feedback file's own
   fingerprint) in `models/manifest.json`.
3. If the guardrail rejects the candidate, production is left completely
   untouched — verified by test
   (`tests/test_train_production.py::test_rejected_candidate_leaves_production_untouched`)
   to be byte-identical, not just "probably fine."

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

---

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
| `scripts/verify_model_manifest.py` | The CI model gate — artifact SHA-256 + recorded F1 + deployment-file threshold agreement. |
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

---

## L1 — Serving API

FastAPI, `api/app.py`. Serves the hybrid v3/v4 decision, root-cause ranking, the feedback loop, and the mission-control operations console.

### What we tested

FastAPI service, model baked into the Docker image. Rebuild cost is
seconds, so image tag = exact model version.

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

---

### Control panel (mission-control UI)

A single web console served by the API at `/ui` that drives the entire
platform by clicking, not commands — built as the live-demo and operator
interface. It calls the existing `/predict`, `/root-cause`, `/machines`,
and `/feedback` endpoints (it does not alter any serving logic) plus small
read-only support endpoints (`/ui/status`, `/ui/metrics`, `/ui/infra`).

**Access is now gated.** `/ui/*` returns `404` unless `ENABLE_OPS_UI=1` is
explicitly set — never set on the Kubernetes serving pods — and requires
HTTP Basic auth, compared in constant time, when enabled. This is a real
change from the console's original, unauthenticated design; see
[Security posture](#security-posture-new).

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
- **Demo** — send batches of **real, held-out test readings** and score the
  model live: a table of real-vs-predicted with a TP/TN/FP/FN verdict per
  row, a running precision/recall/accuracy scorebar, and an expandable
  per-prediction detail.
- **Infrastructure** — Docker containers and images, Kubernetes pods (image
  tags, restart counts), `kubectl top`, HPA status, Prometheus target
  health, and the machines currently flagged anomalous.
- **Project** — a results summary.

#### Honest demo evaluation, and a bug it exposed

The Demo tab is deliberately built to be *honest*, and getting there
surfaced a real, instructive bug. Two design rules make it trustworthy:

1. **It evaluates on genuinely held-out data.** The readings are sampled
   from `data/telecom_fleet_v2_test.csv` — the same independent seed-123
   test set used for every evaluation number in this document — never seen
   during training. The sampler seeks across the whole file so all machine
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

---

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
[Security posture](#security-posture-new).

---

### Files

| File | What it does |
|---|---|
| `api/app.py` | The FastAPI application — every endpoint above, input validation, Prometheus metrics, artifact-integrity checking before unpickling, and the ops-UI guard. |
| `api/feedback_db.py` | PostgreSQL access for logging predictions and recording operator verdicts. |
| `api/training.py` | Backend for the training console — dataset selection (with the path-traversal fix), representative sampling, comparing a candidate against production. |
| `api/static/control_panel.html` | The mission-control UI, served at `/ui`. |
| `tests/test_api.py` (27 tests), `test_decision.py`, `test_training_console.py` | Covered in full in [Testing](#testing) below. |

---
## Security posture (new)

Added in the most recent review pass, consolidating every hardening measure
across the whole platform in one place:

| Area | Measure |
|---|---|
| Credentials | Nothing committed. PostgreSQL, Grafana, MinIO, and the ops-UI password are all generated randomly or supplied from the environment at deploy time. A real committed password (`feedback-dev-password`, in the now-deleted `kubernetes/postgres-secret.yaml`) was found, removed, and rotated live in this VM's actual running database — see [Storage](#storage) above for the full rotation story. |
| Containers | Both the API and PostgreSQL run as non-root, with `readOnlyRootFilesystem`, all Linux capabilities dropped, `seccompProfile: RuntimeDefault`, and `automountServiceAccountToken: false`. |
| Operations console | `/ui/*` does not exist (404) unless `ENABLE_OPS_UI=1` is explicitly set — the Kubernetes serving pods never set it. When enabled, it requires HTTP Basic auth, compared in constant time, and fails closed (`503`) if enabled without a password configured. This console previously had no authentication of any kind. |
| Supply chain | Every production model artifact's SHA-256 is verified against `models/manifest.json` before it is unpickled — both in the API and in CI. A CycloneDX SBOM is generated and archived on every image build. |
| Image | Python 3.12 on Debian trixie (was 3.10-slim); `pip` and `ensurepip` are removed from the final image after dependencies are installed. |
| Input handling | Path traversal in the training console's dataset selector closed. `NaN`/`infinity` rejected at the API boundary. `history` validated strictly (exact keys, exact length). |
| Scanning | Trivy scans both the repository (dependencies, committed secrets, Kubernetes/Dockerfile misconfigurations) and the built image, both blocking (`--exit-code 1`) in CI. |
| Secrets in transit | The PostgreSQL Secret is assembled by Ansible and applied via `stdin`, never a command line or a shell pipe. |

---

## L2 — Container Image

### What we did

- Base: `python:3.12-slim-trixie` (upgraded from `3.10-slim`), non-root `appuser` (uid/gid 10001), layer-cached deps, model and dependency graph baked into the image.
- `pip` and `ensurepip` are removed from the final image after dependencies install — neither is needed at runtime, and their presence would be an unnecessary attack surface.
- `HEALTHCHECK` on `/health`, same endpoint K8s liveness probes use.
- Local registry (`registry:2`, port 5000) for Jenkins to push to.

```bash
docker build -t devsecmlops-api:$(cat VERSION) .
docker run -p 8000:8000 devsecmlops-api:$(cat VERSION)
```

### Real bugs found and fixed

- **A version pin in a `RUN` command, `wheel>=0.46.2`, was unquoted** — the shell interpreted `>` as a file redirect, so `wheel` was actually installed completely unpinned, silently, into a file literally named `=0.46.2`.
- **The comment `# Copy the ONE shipped model artifact` preceded copying 6+ files** (v3/v4 XGBoost, encoders, IsolationForest, baselines, dependency graph, manifest) — misleading for anyone reading the Dockerfile to reconstruct what's actually there. Corrected to describe what's real: `# Copy production inference artifacts (v3/v4 XGBoost + IsolationForest safety net)`.
- **The legacy `MODEL_NAME=telecom_v2` path in `docker-entrypoint.sh` defaulted to `MINIO_ACCESS_KEY=admin` / `MINIO_SECRET_KEY=minioadmin123` when unset**, substituted directly into a real boto3 client — silently connecting with publicly-known credentials rather than failing. Fixed to fail closed (`: "${VAR:?message}"`), consistent with this project's security posture everywhere else. Verified live: unsetting both vars now genuinely aborts with a clear error instead of silently proceeding.

### Files

| File | What it does |
|---|---|
| `Dockerfile` | The image build — Python 3.12, non-root, hardened, model artifacts baked in. |
| `docker-entrypoint.sh` | Startup script; only does real work for the legacy `telecom_v2` path (MinIO fetch), fails closed if MinIO credentials aren't supplied. |
| `.dockerignore` | Excludes `venv/`, `data/`, old models from the build context. |
| `requirements-api.txt` | Pinned, curated dependencies — the same versions the host venv uses, since pickled models must be loaded by the version that wrote them. |

---

## L3 — CI/CD

Jenkins, `Jenkinsfile`, 11 stages, every one a real gate — a failure stops the pipeline before anything downstream is built, pushed, or deployed. The pipeline used to have 9-10 stages with two structural weaknesses: the SonarQube Quality Gate could not actually stop the build (`abortPipeline: false`), and Trivy's scan result was discarded (`|| echo "... non-blocking"`). Both are fixed.

### What we built

Stage labels below match the Jenkins console exactly.

- **1. Checkout**
- **1b. Unit tests** — pytest, 58 tests, coverage report. Fail-fast: a broken commit stops here.
- **2. SAST** — SonarQube.
- **2b. Quality Gate** — `abortPipeline: true`.
- **3. Repository scan + model gate** — `trivy fs` on the repository itself (dependencies, secrets, IaC misconfigurations), and `scripts/verify_model_manifest.py --min-f1 0.60`, checking artifact integrity, the recorded F1, and that every deployment file uses the evaluated threshold — *before* an image is built.
- **4. Build Docker image**
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

Runs 1 and 2 are the evidence that the gate genuinely blocks: every later stage was skipped, nothing was built, pushed, or deployed.

**Earlier-cycle fixes, kept as real history:** 2 HIGH CVEs (CVE-2026-24049 wheel, CVE-2026-23949 jaraco.context) found by Trivy and fixed by version pinning; a SonarQube cycle that flagged a **hardcoded PostgreSQL password (Blocker)** and a cognitive-complexity violation on `predict()`, both fixed; a smoke-test robustness fix so the container serves `/health` even with no database reachable.

### Real bugs found and fixed

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
  [Security posture](#security-posture-new); `PREDICT_THRESHOLD` and the DB
  credentials come from the environment/Secret, never hardcoded.
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
| `scripts/ensure_db_secret.sh` | Generates a random DB password idempotently (demo profile). |
| `scripts/k8s_recover.sh` | Recovers Minikube and Jenkins's cluster access after a Docker daemon or VM restart. |

---

## L5 — Observability and MLOps

Prometheus + Grafana (30 panels) + real production traffic + Kubernetes
object monitoring.

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

#### Grafana dashboard (30 panels)

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

### MLflow and MinIO (experiment tracking, model registry)

MLflow (tracking + model registry) backed by MinIO (self-hosted
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

#### Every host process is now a supervised systemd unit, in both profiles (real change)

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

#### MLflow now provisioned and supervised (real change)

MLflow runs as a systemd service (`:5001`) with `--serve-artifacts`,
proxying uploads to MinIO — see [MLflow and MinIO](#mlflow-and-minio-experiment-tracking-model-registry)
above for the real round-trip verification. It was not consistently running
before this pass.

#### Credentials from the environment, root-only files (real change)

Every credential — the Grafana admin password, the ops-UI Basic-auth
credentials, the MinIO root credentials — is required from the environment
at playbook run time (a 12-character-minimum `assert`), written to
root-only files (`0600`, under `/etc/devsecmlops/`), and never committed or
placed directly in a systemd unit file. The PostgreSQL Secret is assembled
in Ansible with base64-encoded values and applied to Kubernetes via
`stdin` — the password never appears on a command line or in the process
list.

#### Jenkins gets the data mount it needed (real change)

The Jenkins container now has `data/` mounted read-only at
`/var/jenkins_home/fleet-data` — the prerequisite `Jenkinsfile.model`
needed and did not have before this pass. The Jenkins image tag, and the
API image tag, both now derive from the `VERSION` file rather than
separate hardcoded values that had drifted out of sync with each other.

#### A real bug found only by running it

`repo_dir` was defined as `"{{ playbook_dir }}/.."`, which resolves to an
unnormalized path (e.g. `.../ansible/..`). systemd rejects this in
`WorkingDirectory=` ("path is not normalized"). Fixed with a `| realpath`
filter. This would have affected every systemd unit this project installs,
not only MLflow — it was only caught here because MLflow was the first
genuinely *new* unit this pass introduced; every pre-existing unit had
silently carried the same latent bug without being re-validated.

#### Verified with a real playbook run, not just a syntax check

`--syntax-check` on both profiles, then a `--check` dry run, then a real
run reaching `PLAY RECAP ... failed=0, ok=56, changed=12`. Confirmed
afterward against the live control panel: **11 of 11 platform layers up**,
including MLflow running for the first time in this project's development,
and confirmed with direct checks that the auth enforcement (`401` with no
credentials, `401` with a wrong password, `200` only with the real one) and
service persistence (`systemctl is-enabled` reporting `enabled` for every
new unit) genuinely work, not just that the playbook exited successfully.

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

#### Honest design notes, carried forward

- **SonarQube hostname**: from the host, `http://localhost:9000`; from
  inside the Jenkins container, `http://sonarqube:9000` (Docker resolves
  the container name). Both correct in their respective contexts.
- **Image loading**: the API image is loaded into Minikube directly
  (`minikube image load`, `imagePullPolicy: Never`) rather than pulled from
  the local registry, for the same single-node reasons documented in
  [L4 — Kubernetes](#l4--kubernetes).
- **Scope**: Ansible automates the *platform bring-up*, not base-OS package
  installation — the VM is expected to already have Docker, Minikube,
  kubectl, and the Prometheus binary, with the repo cloned.

#### Every support container now declares its network (real fix, found by a failed CI build)

`registry`, `minio`, and `sonarqube` were defined in `roles/prerequisites` with ports, volumes, and restart policy, but **no `networks:` key at all** — so `community.docker.docker_container` placed each one on Docker's default `bridge` network every time it (re)created them. Jenkins reaches all three by container name over `devsecmlops-net`, so after an Ansible run recreated them, a Jenkins build failed with `sonarqube: Name or service not known`. Confirmed live before fixing: `docker inspect` showed all three on `bridge` only; `docker exec jenkins getent hosts sonarqube` returned nothing. Fixed by declaring `networks: [devsecmlops-net]` on all three tasks. Verified with a real playbook run (not `--check`): all three now on `devsecmlops-net`, and a live DNS lookup from inside the Jenkins container resolves `registry`, `sonarqube`, and `minio` by name. The next Jenkins build passed end to end.

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

### 10. Why dual-model architecture (primary + safety net)

RandomForest is blind to cascades by construction (see previous). IsolationForest, being unsupervised, has no such blind spot -- it reacts to any statistical deviation regardless of label noise (0.262 cascade recall). Running both means the primary gets high accuracy on the 5 clean cause types AND cascades still get detected via the safety net. Two rejected alternatives:

- **Blind ensemble (flag if either fires)**: F1 = 0.663, worse than RandomForest alone. Inherits IsolationForest's false positives without helping recall.
- **Graph-rule cascade fix (flag all downstream when router flagged)**: F1 = 0.550. Blast radius: each router has ~22 downstream machines; one wrong router prediction = ~22 wrong flags. Cascade-rule precision on fired rows: 2.9%.

Both rejections are documented with real numbers, not hand-waved. This remains the shipped architecture today: the served decision is still `classifier OR IsolationForest` (`ml-model/decision.py`), unchanged in principle since this decision was made, though the classifier and the specific safety-net numbers have moved on since (see [L0 — ML Model](#l0--ml-model) and the [unsupervised-model comparison](#the-unsupervised-model-comparison-updated) below).

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
F1 (independent seed-123 test set, 17,280,000 rows, current threshold 0.60)
went from 0.6496 (v3) to 0.7246 (v4) -- see [L0 — ML Model](#l0--ml-model) for
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

F1 = 0.269 on the Server Machine Dataset (28 real servers, public benchmark used by OmniAnomaly and other sequence-model papers). Much lower than synthetic F1, and this is the honest point: **published unsupervised sequence models on SMD report F1 in the 0.40-0.55 range** using recurrent architectures that read a sliding window of history. This project prioritizes training speed, interpretability, and simple CI/CD-integrated deployment over the marginal accuracy gains of a recurrent sequence model -- a documented tradeoff, not an oversight. The SMD result's purpose is not to win on the benchmark; it confirms the same per-machine, per-window z-score methodology generalizes to real, independently-collected data and isn't an artifact of the synthetic generator. `ml-model/load_smd.py` is the adapter that makes this data loadable through the same pipeline; it remains in the repository and is not part of the production training path.

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

### 20. Why the decision threshold is 0.60, selected by method (revised)

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
enforces that every deployment file agrees with it. See
[Choosing the decision threshold](#choosing-the-decision-threshold-new) below
for the full table. Configurable via the `PREDICT_THRESHOLD` env var without
code changes, as before.

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
handling further -- see [Security](#security-posture-new) and
[L4 — Kubernetes](#l4--kubernetes) below.

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

---

## Testing

**58 tests** (up from 15), `pytest`, in `tests/`:

- `test_api.py` (27 tests) — every endpoint, input validation (NaN/inf
  rejection, machine length, metrics count, history shape), v3/v4
  auto-selection, Prometheus metrics, DB-unavailable behavior (503, not
  500), ops-UI auth (required, wrong password rejected, absent when
  disabled), and artifact-integrity rejection of a tampered model file.
- `test_decision.py` — the scalar and vectorized forms of the served
  decision rule produce identical results, at multiple thresholds, with and
  without per-class overrides.
- `test_preprocess.py` — baseline construction, the fallback chain, z-score
  correctness, the standard-deviation floor that prevents division by zero.
- `test_train_production.py` — the full training pipeline: promotion with
  correct lineage, the CI gate accepting a genuinely promoted model and
  rejecting a tampered or low-F1 one, retraining determinism, a rejected
  candidate leaving production byte-for-byte untouched, and feedback rows
  with an unrecognized cause skipped rather than crashing.
- `test_training_console.py` — the dataset whitelist (rejects path
  traversal in every form tried), that sampling genuinely spreads across
  the whole file, and that a candidate is compared against production on
  the same rows.

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

# Train via the canonical path
python ml-model/train_production.py --train data/telecom_fleet_v2_labeled.csv \
  --test data/telecom_fleet_v2_test.csv --promote --report build/report.json

# Select the decision threshold by method
python ml-model/select_threshold.py

# Benchmark the unsupervised safety net against 5 alternatives
python ml-model/benchmark.py --data data/telecom_fleet_v2_test.csv --max-rows 0

# Run the z-score demo (defense script)
python ml-model/zscore_demo.py

# Serve directly
MODEL_NAME=telecom_v3 uvicorn api.app:app --host 0.0.0.0 --port 8000

# Or containerized
docker build -t devsecmlops-api:$(cat VERSION) .
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

---

## Documentation and evidence

| Path | What it is |
|---|---|
| `docs/TROUBLESHOOTING.md` | A runbook written from real incidents: Docker networking staleness, Minikube API timeouts, a rejected retrain (the guardrail working as intended), MLflow service checks, browser-vs-VM connectivity, Jenkins pytest/venv failures. Corrected in this pass: it referenced the deleted `retrain_from_feedback.py` and told operators to hand-launch MLflow with hardcoded MinIO credentials. |
| `docs/machine_roster.txt` | The 200 simulated machines and their roles. |
| `screenshots/` | 12 PNGs captured at real milestones: the six-model benchmark, the final training run, a three-way model experiment, the z-score demo, the data generator, `/health`, a "3am problem" scenario, a disk-usage anomaly, the baseline fallback chain, Docker images, a container health check, a registry push. |

---

## Known limitations and future work

- **`cascade` recall (0.28) is genuinely the weakest result in this
  project**, stated honestly rather than hidden — the generator's own
  cascade labeling is inconsistent row to row, which is why it is trained
  as `normal` and relies on the IsolationForest for coverage instead. See
  [Engineering decision 9](#9-why-cascade-is-folded-into-normal-during-training).
- **v4's rolling features cover 3 of 6 metrics** (`cpu`, `ram`, `load_avg`)
  — the ones where a *trend* carries more signal than a snapshot. Extending
  to all six is real, larger work (retraining v4 from scratch, re-selecting
  the threshold, re-evaluating), not attempted in this pass.
- **`Jenkinsfile.model` has not been run for real.** Its infrastructure
  prerequisite (the `data/` mount) is done; it still needs a
  `github-pat-devsecmlops` Jenkins credential before a real retraining
  cycle can be exercised end to end.
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
