# Setting up the whole platform on a new machine

This guide takes a blank Ubuntu machine to the running platform: the CI/CD tooling, the Kubernetes serving layer, the monitoring stack and the control panel. It was written from the repository's code (the Ansible roles, the Jenkinsfiles, the Dockerfiles) and from the way the original VM was brought up.

**Read the status table first.** The platform has been brought up for real on one machine (the original VM). It has **not** been brought up on a clean second machine, so every step below carries a label saying how well it is known.

| Label | Meaning |
|---|---|
| **VERIFIED** | Run on the original VM and seen to work (README, "What we tested") |
| **FROM CODE** | Derived from the Ansible roles / Jenkinsfiles; not run on a clean machine |
| **MANUAL** | Not automated by Ansible; the original VM was set up by hand and the settings live only in Docker volumes |

---

## 0. What you need

| Item | Value | Label |
|---|---|---|
| OS | Ubuntu (the original VM runs Ubuntu with host Python 3.10) | VERIFIED |
| CPU / RAM | 8 threads, 16 GB RAM or more. The full model job needs about 12 GB free; SonarQube and Minikube need several GB each | VERIFIED |
| Disk | 100 GB free or more (estimate). Every Jenkins build leaves a ~2 GB image; SonarQube turns read-only at 95% disk use | FROM CODE / README |
| Internet | `get.docker.com`, `dl.k8s.io`, GitHub (Prometheus release, the repository), Docker Hub, PyPI, Minikube's download host | FROM CODE |
| Account | A GitHub personal access token with write access to the repository (the model job pushes the promoted model) | FROM CODE |

All commands below run as root (`sudo -i`), as on the original VM.

---

## 1. Base packages  (FROM CODE)

```bash
sudo -i
apt-get update
apt-get install -y git curl ca-certificates python3 python3-venv python3-pip ansible
mkdir -p /home/pfe          # the Ansible variables and the Jenkins container expect /home/pfe/.kube and /home/pfe/.minikube
ansible --version | head -1
```

The Ansible Docker modules also need the Docker SDK for Python on the machine. Install the **same version as the original VM (7.1.0)** with pip, then clone the repository and install the Ansible collection:

```bash
# Ubuntu 22.04 (the original VM; Python 3.10)
pip3 install docker==7.1.0
# Ubuntu 24.04 refuses plain pip installs; use this line instead
# pip3 install --break-system-packages docker==7.1.0
python3 -c "import docker; print('docker SDK', docker.__version__)"     # must print 7.1.0

git clone https://github.com/aminshil/devsecmlops-pfe.git /root/devsecmlops-pfe
cd /root/devsecmlops-pfe
ansible-galaxy collection install -r ansible/requirements.yml
ansible-galaxy collection list community.docker | tail -2
```

The `system` role also lists the apt package `python3-docker` as a safety net. When the pip version above is present it takes priority, so the two do not conflict.

The original VM was set up at `~/devsecmlops-pfe` as root. If you use another path, nothing in the playbook is tied to `/root`; it derives the repository directory from its own location. `/home/pfe` is the only fixed path.

---

## 2. Python environment and tests  (VERIFIED)

```bash
cd /root/devsecmlops-pfe
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q tests          # expect: 179 passed
```

If `179 passed` does not appear, stop here. Nothing else will work reliably.

---

## 3. The four datasets  (VERIFIED for three of the four, on a second machine)

The datasets are not in git (`data/*.csv` is ignored). They come from the project's own generator, `ml-model/generate_telecom_fleet.py`. Each fleet has its own seed, so the three files share no rows; they do describe the same simulated fleet (same machine names and calendar) with independent noise, which is why a held-out score here is optimistic compared with a genuinely new site.

| File | Used for | Command |
|---|---|---|
| `data/telecom_fleet_v2_labeled.csv` | training | `--seed 42` |
| `data/telecom_fleet_v2_val.csv` | choosing the threshold | `--days 14 --seed 7` |
| `data/telecom_fleet_v2_test.csv` | evaluation, the demo traffic, live validation | `--seed 123` |
| `data/telecom_fleet_v2_operator.csv` | the control panel's operator stream | `--days 3 --seed 2026` |

```bash
cd /root/devsecmlops-pfe && source venv/bin/activate
G=ml-model/generate_telecom_fleet.py
python3 $G --machines 200 --days 30 --seed 42   --output data/telecom_fleet_v2_labeled.csv
python3 $G --machines 200 --days 14 --seed 7    --output data/telecom_fleet_v2_val.csv
python3 $G --machines 200 --days 30 --seed 123  --output data/telecom_fleet_v2_test.csv
python3 $G --machines 200 --days 3  --seed 2026 --output data/telecom_fleet_v2_operator.csv
wc -l data/telecom_fleet_v2_*.csv
```

Expected line counts (header included): labeled and test 17,280,001; validation 8,064,001; operator 1,728,001.

**Memory.** Generating a 30-day fleet at 30-second resolution was killed by the out-of-memory killer once on a 7.7 GB machine (README, "Data resolution"). Run the generators one at a time and keep 8 GB or more free.

**Reproducibility note.** The training run records the SHA-256 of the training and test files in `models/manifest.json`:

```text
train  b4a8080965f4f0bc12b2727916969c6fe728a4dcb78bb169616155bba16e43cf
test   3b6b6e2144100d43522a79a9a685eecc49e2bcecc573b001584614f0a5a8894a
val    ea0a20c29f8d3e34a77c8db79abc43ccd0d051d5049003cda659e0194c1148a0
```

```bash
sha256sum data/telecom_fleet_v2_labeled.csv data/telecom_fleet_v2_test.csv data/telecom_fleet_v2_val.csv
```

**Checked on a second machine.** The training (`--seed 42`), validation (`--seed 7 --days 14`) and test (`--seed 123`) fleets were regenerated from scratch in a different environment (Ubuntu 24.04, Python 3.13, numpy 2.5.3, pandas 2.3.3, 2 CPUs, 7.8 GB RAM). All three produced **exactly** the SHA-256 values above, and each took a few minutes. So a new machine can rebuild the original datasets byte for byte, and the scores in the README apply to them. The operator fleet (`--seed 2026`) has no recorded hash, so it was not compared.

If your hashes differ anyway (another numpy or pandas version changes the random stream), the files are still valid datasets from the same generator. The model job records the new hashes and the scores will differ slightly from the README.

---

## 4. Credentials  (FROM CODE)

Nothing secret is stored in the repository. The playbook reads passwords from the environment and refuses to run if they are missing or shorter than 12 characters (MinIO, Grafana and the Operations UI). Choose your own values and keep this file **outside the repository**:

```bash
install -m 600 /dev/null /root/.devsecmlops.env
cat > /root/.devsecmlops.env <<'EOF'
export MINIO_ROOT_USER='choose-a-user'
export MINIO_ROOT_PASSWORD='choose-12-or-more-chars'
export GRAFANA_ADMIN_PASSWORD='choose-12-or-more-chars'
export OPS_UI_USER='operator'
export OPS_UI_PASSWORD='choose-12-or-more-chars'
# optional, NOT wired into Ansible, leave it out for the demo. To protect POST /feedback:
#   kubectl set env deployment/anomaly-api FEEDBACK_TOKEN=<value> -n ml-serving
# and give the same variable to the control panel and scripts/feedback_demo.py, otherwise their verdicts get a 401
# export FEEDBACK_TOKEN='choose-a-long-random-value'
EOF
nano /root/.devsecmlops.env       # replace every placeholder
source /root/.devsecmlops.env
for v in MINIO_ROOT_USER MINIO_ROOT_PASSWORD GRAFANA_ADMIN_PASSWORD OPS_UI_PASSWORD; do [ -n "${!v}" ] && echo "$v set" || echo "$v MISSING"; done
```

Never paste the contents of this file into a chat, a ticket or git. The production profile also needs the database password in an Ansible Vault (`ansible-vault create group_vars/production_secrets.yml` containing `vault_db_password`); the demo profile generates a random database password on its own.

---

## 5. Run Ansible  (VERIFIED on the original VM; first-run order is FROM CODE)

```bash
cd /root/devsecmlops-pfe/ansible
ansible-playbook -i inventory.ini site.yml --syntax-check
```

Optional dry run (changes nothing; the bucket task and all installs are skipped). Use throwaway values of 12+ characters, **never run the real playbook with them**: MinIO would be recreated with the wrong credentials.

```bash
MINIO_ROOT_USER=x MINIO_ROOT_PASSWORD=placeholder-12345 GRAFANA_ADMIN_PASSWORD=placeholder-12345 OPS_UI_USER=x OPS_UI_PASSWORD=placeholder-12345 ansible-playbook -i inventory.ini site.yml --check
```

Expect `failed=0`. Some `changed` lines are normal (apt, pip, and the MinIO and Grafana containers, which differ from your real values).

### 5a. First pass: tooling, image and Kubernetes only

On a clean machine run **this pass first**. Reason (read from the roles, not tested): in the demo profile the `prerequisites` role runs before the `kubernetes` role, and one of its tasks attaches Jenkins to a Docker network called `minikube`. On a machine where Minikube has never started that network does not exist yet, so Ansible would create a plain one, and Minikube would then find a network of its own name with the wrong address range. Starting Minikube first avoids this.

```bash
source /root/.devsecmlops.env
ansible-playbook -i inventory.ini site.yml --tags system,app_setup,kubernetes 2>&1 | tail -30
```

This installs Docker, kubectl, Minikube and Prometheus (pinned versions; skipped when already present), creates the virtualenv, builds the API image, starts Minikube and deploys PostgreSQL and the API. Expect `failed=0`.

```bash
export KUBECONFIG=/home/pfe/.kube/config
kubectl get pods -n ml-serving          # 2 anomaly-api pods and postgres, all Running
docker network inspect minikube --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'    # expect 192.168.49.0/24
```

The cluster address is then `http://192.168.49.2:30080` (the NodePort used by the live validation script and the README).

### 5b. Second pass: everything

```bash
ansible-playbook -i inventory.ini site.yml 2>&1 | tail -40     # demo profile (default)
```

This starts the registry, MinIO (and creates its `mlflow-artifacts` bucket if missing, via `scripts/ensure_minio_bucket.py`), MLflow, SonarQube, the custom Jenkins image, Grafana, node-exporter, the systemd units (Prometheus, k8s-exporter, production agent, control panel) and the Jenkins network unit.

The production profile (`-e profile=production --ask-vault-pass`) skips Jenkins, SonarQube, MinIO, MLflow and the control panel, runs 3 replicas, and uses a Vault-backed database secret.

### 5c. Check it

```bash
systemctl is-active prometheus k8s-exporter production-agent control-panel mlflow
docker ps --format '{{.Names}}  {{.Status}}'
curl -s -o /dev/null -w "control panel %{http_code}\n" http://localhost:8000/
curl -s http://192.168.49.2:30080/health | python3 -c "import sys,json; d=json.load(sys.stdin); l=d['model_lineage']; print(d['status'], d['version'], l.get('threshold'), '|', l.get('threshold_source'))"
```

Expected: five `active`; containers `registry minio sonarqube jenkins grafana node-exporter` plus `minikube`; `/health` prints `ok`, the version, `0.91` and `models/manifest.json production.decision_threshold`.

---

## 6. One-time Jenkins and SonarQube configuration  (MANUAL)

This is the part Ansible does **not** do. Jenkins' jobs, credentials and plugins, and SonarQube's project and token, live in Docker volumes (`jenkins_home`, `sonarqube_data`) on the original VM. They are not in the repository, so a new machine starts with empty Jenkins and SonarQube. The names below are the ones the Jenkinsfiles use.

### 6a. SonarQube  (`http://<host>:9000`)

1. Log in with `admin` / `admin` and set a new password when asked.
2. Create a project with the key **`devsecmlops-pfe`** (Jenkinsfile: `-Dsonar.projectKey=devsecmlops-pfe`).
3. Create a user token (My Account, Security) and keep it for 6b.
4. Administration, Configuration, Webhooks: add a webhook to `http://jenkins:8080/sonarqube-webhook/`. Without it the quality-gate stage waits until its 3-minute timeout.
5. Quality Gates: the original used a blocking gate (the pipeline aborts on failure). Check which gate the original project used and apply the same one.

### 6b. Jenkins  (`http://<host>:8080`)

1. Unlock: `docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword`
2. Install plugins: Pipeline, Git, Credentials Binding, SonarQube Scanner for Jenkins (the Jenkinsfiles use `checkout scm`, `withCredentials`, `withSonarQubeEnv`, `waitForQualityGate`, `archiveArtifacts`, `build`).
3. Manage Jenkins, Credentials, add:
   - id **`github-pat-devsecmlops`**: Username with password (your GitHub user name and the token). Used for checkout and for pushing the promoted model.
   - a Secret text credential holding the SonarQube token from 6a.4.
4. Manage Jenkins, System, SonarQube servers: name **`sonarqube`**, URL **`http://sonarqube:9000`**, token = the credential above.
5. Create two Pipeline jobs, each "Pipeline script from SCM", Git, repository `https://github.com/aminshil/devsecmlops-pfe.git`, branch `main`, credential `github-pat-devsecmlops`:
   - **`devsecmlops-pipeline`**, script path `Jenkinsfile`
   - **`devsecmlops-model`**, script path `Jenkinsfile.model` (its parameters `SCOPE`, `INCLUDE_FEEDBACK`, `PROMOTE` are declared in the file and appear after the first run)

These steps follow what the Jenkinsfiles require. They have not been replayed on a blank Jenkins, so expect to fix small details (a plugin name, a menu label).

---

## 7. First builds and final check  (FROM CODE)

1. Run **`devsecmlops-pipeline`** with Build Now. SonarQube must be running first. About 12 minutes; all 11 stages should pass, including the post-deploy smoke test, which fails unless the alert threshold is read from the manifest. If that smoke test fails, the pipeline rolls the deployment back to the previous revision on its own (`kubectl rollout undo`).
2. Optional, to prove the retraining path: run **`devsecmlops-model`** with `SCOPE=smoke` (about 5 minutes, no memory risk, production untouched). Run `SCOPE=full` only with 12 GB free and SonarQube, Grafana and the exporters stopped.
3. Checklist:

```bash
cd /root/devsecmlops-pfe && source venv/bin/activate
python3 scripts/verify_model_manifest.py --min-f1 0.60      # the CI model gate: artifact hashes, F1, threshold
python3 scripts/live_k8s_validation.py --days 14 --every-hours 4 --out build/live_validation.json 2>&1 | tail -15   # about 5 minutes, needs the test dataset
```

The README states the expected results.

---

## 8. Day-to-day  (VERIFIED)

| Task | Command |
|---|---|
| Survive a reboot | Automatic: the host services are systemd units and the containers restart on their own. `jenkins-networks.service` re-attaches Jenkins to its networks |
| Free memory for a full model run | Stop SonarQube, Grafana, node-exporter, production-agent, k8s-exporter, Prometheus. Keep Jenkins, MinIO, MLflow, Kubernetes |
| Disk | `df -h /` before a demo; prune old images (`docker image prune -a` after checking what you need). SonarQube goes read-only at 95% |
| Change training size or the recall target | Edit **only** `config/model_policy.json` |
| Rotate credentials | Change the values in `/root/.devsecmlops.env`, re-run the playbook (this rewrites the root-only files and restarts the units), then change Grafana's and SonarQube's passwords in their UIs |

### Optional hardening (written, apply by hand; not part of the default run)

**NetworkPolicy check.** `kubernetes/networkpolicy.yaml` lets only the API pods reach PostgreSQL. It takes effect only if the cluster's network plugin enforces policies, so prove it:

```bash
kubectl apply -f kubernetes/networkpolicy.yaml
kubectl -n ml-serving run np-test --rm -i --restart=Never --image=postgres:16-alpine -- pg_isready -h postgres -t 5    # expect: no response (blocked)
kubectl -n ml-serving exec deploy/anomaly-api -c api -- python3 -c "import socket;socket.create_connection(('postgres',5432),3);print('api reaches postgres')"   # expect: api reaches postgres
```

If the first command still answers "accepting connections", the plugin does not enforce policies: the file does nothing, so delete it (`kubectl delete -f kubernetes/networkpolicy.yaml`) and do not claim it in the defense.

**Vault file for the production profile:** `scripts/make_production_secrets.sh` (random password, encrypted, refuses to overwrite). **Feedback token:** `export FEEDBACK_TOKEN=...` before the playbook creates the `feedback-token` Secret and gives the control panel the same value; without it `/feedback` stays open. **CI settings as files:** `python scripts/ci_config.py export` (read-only) writes `ci-config/` from the running Jenkins and SonarQube; `restore --yes` replays the jobs. Both need `JENKINS_USER`, `JENKINS_TOKEN` and `SONAR_TOKEN`; the export script is tested against a fake server only.

---

## 9. Known gaps for a clean-machine setup

| Gap | State |
|---|---|
| Ansible collection `community.docker` and the Docker SDK for Python were not declared | Fixed alongside this guide: `ansible/requirements.yml` for the collection; the SDK is pinned to the original VM's version (`docker==7.1.0`, section 1), with `python3-docker` in the `system` role as a fallback |
| First-run network order (section 5a) | Worked around by running the tags `system,app_setup,kubernetes` first; the playbook itself is unchanged |
| Jenkins jobs, credentials, plugins and the SonarQube project are not codified | MANUAL (section 6). Jenkins Configuration as Code would remove this; not done |
| Datasets are regenerated, not stored | Checked: the training, validation and test fleets regenerate to the original hashes on a second machine (section 3) |
| `/home/pfe` is a fixed path | Create it, or change `kubeconfig_path`, `minikube_home` and the Jenkins volume mounts together |
| The MLflow artifact bucket was never created, and `boto3` was not declared for the host | Fixed: `scripts/ensure_minio_bucket.py` (called by the `prerequisites` role; skipped in `--check`) and `boto3` in `requirements.txt` |
| `ansible-playbook --check` skipped the read-only probes, so it guessed | Fixed: the probes run for real in `--check`. The dry run passes on the original VM (`failed=0`) |
| `group_vars/production_secrets.yml` is not in the repository | By design (it holds a secret). The demo profile does not need it; the production profile stops with a clear message until you create it with `ansible-vault create` (section 4) |
| A new `docker` group membership does not apply to a running play | The play uses a local connection and cannot refresh it. On a non-root account, log out and in (or `newgrp docker`) after the first Docker install and re-run |
| Never run on a clean second machine | True. Treat this guide as a careful reading of the code, not a tested procedure |
