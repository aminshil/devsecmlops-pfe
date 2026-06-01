# DevSecMLOps Cloud-Native Platform

End-of-studies project — ESPRIT × Tunisie Telecom (Jan–June 2026).

Open-source platform automating the secure ML lifecycle: from a Git commit
to a monitored anomaly-detection model in production. Use case: anomaly
detection on IT infrastructure KPIs (CPU, RAM, network) via Isolation Forest.

## Stack
Jenkins · Docker · Kubernetes · Ansible · Python/FastAPI · scikit-learn ·
MLflow · MinIO · SonarQube · Trivy · Prometheus · Grafana

## Architecture pillars
1. **Automation** — end-to-end CI/CD from commit to deployed model
2. **Reproducibility** — every experiment and artefact versioned
3. **Security** — SAST + image scanning as pipeline gates
4. **Observability** — metrics, alerts, drift detection, auto-retraining

## Current status — POC

**M1-W3 (POC validation):** Synthetic data generator + Isolation Forest baseline.

- Dataset: 1000 samples, 5% anomalies (950 normal, 50 anomaly)
- Model: scikit-learn IsolationForest, 100 estimators, contamination=0.05
- Result: **F1-score = 0.94** on synthetic data (target: ≥ 0.85)

### Run the POC
```bash
source venv/bin/activate
python ml-model/generate_data.py    # writes data/data.csv
python ml-model/train.py            # trains, evaluates, saves models/model.pkl
```

### Next milestones
- **M1-W4:** Validate on real data (Server Machine Dataset)
- **M2:** Wrap training in Jenkins CI/CD pipeline
- **M3:** Log experiments to MLflow, store artifacts in MinIO

## Repo layout
- `ml-model/`     training code, notebooks, model artefacts
- `api/`          FastAPI inference service
- `data/`         datasets (gitignored)
- `models/`       trained models (gitignored)
- `kubernetes/`   K8s manifests
- `monitoring/`   Prometheus + Grafana configs
- `tests/`        pytest suite
- `scripts/`      utility scripts (training, deploy)
- `docs/`         architecture diagrams, report drafts

## Setup
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt           # runtime deps
pip install -r requirements-dev.txt       # dev/test deps (pytest etc.)
```

## Compliance & constraints
- ISO 27001 alignment (access control, audit, vulnerability management)
- Data sovereignty: all artefacts hosted in-country (MinIO, self-hosted MLflow)
- Network segmentation: K8s NetworkPolicies between training/serving/monitoring
- Data security: TLS in transit, encryption at rest, K8s Secrets

## Author
Amine Shil — amine.shil@esprit.tn

## Supervisors
- Werghemmi Radhia (ESPRIT — faculty)
- Shema Essaddi (ESPRIT — technical expert)
- Sebti Chouchene (Tunisie Telecom — company)
