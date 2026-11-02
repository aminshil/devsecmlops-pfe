# Setting up the whole platform on a new machine

This guide takes a blank Ubuntu machine to the running platform: the CI/CD tooling, the Kubernetes serving layer, the monitoring stack and the control panel.

**It has been run for real on a clean second machine.** On 31 October and 1 November 2026 the platform was brought up from nothing on a rented VPS (netcup VPS 8000 G12: Ubuntu 22.04.5 on Hyper-V, 16 vCPU, 62 GB RAM, 1.9 TB disk, every service used from the VPS itself). The production profile came first, then the demo profile on top. Every step below says how it is known:

| Label | Meaning |
|---|---|
| **VERIFIED (VPS)** | Run on the clean VPS and seen to work, with the result shown |
| **VERIFIED (VM)** | Run on the original VM only (README, "What we tested") |
| **FIXED, re-test** | The VPS run needed a manual step here; the playbook or a script now does it (commit `1b591925`). Confirmed by a re-run on the VPS (2 November), not yet by a second clean install |
| **MANUAL** | Not automated: done by hand in a web UI, the settings live in Docker volumes |

Section 9 lists the 14 gaps the VPS run found, a 15th found afterwards on the original VM, and what became of each.

---

## 0. What you need

| Item | Value | Label |
|---|---|---|
| OS | Ubuntu 22.04 or 24.04. On 22.04 add Python 3.12 (section 1) | VERIFIED (VPS: 22.04.5) |
| Python | **3.12 or newer** for the virtualenv: `requirements-api.txt` pins numpy 2.5.4, and NumPy 2.5 supports Python 3.12 to 3.14 [111]. Ubuntu 22.04's `python3` is 3.10, so pip stops half-way | VERIFIED (VPS) |
| CPU / RAM | 8 threads and 16 GB at least (the original VM). The full model job checks for **9,000 MiB free** before it trains (`Jenkinsfile.model`, preflight); SonarQube and Minikube need several GB each. The VPS had 16 vCPU and 62 GB: nothing had to be stopped | VERIFIED (VM, VPS) |
| Disk | 100 GB free or more. Every Jenkins build leaves a ~2 GB image; SonarQube turns read-only at 95% disk use | README |
| Internet | `get.docker.com`, `dl.k8s.io`, GitHub (Prometheus and MinIO releases, the repository), Docker Hub, PyPI, Minikube's download host, the deadsnakes PPA (22.04 only), `mirror.gcr.io` (Trivy's vulnerability database) | VERIFIED (VPS) |
| Account | A GitHub token for the repository. A fine-grained token limited to this one repository, permission *Contents: read and write*, is enough: Jenkins checks out with it, and the model job pushes a promoted model with it | VERIFIED (VPS) |

All commands run as root (`sudo -i`), as on the original VM and the VPS.

---

## 1. Base packages  (VERIFIED (VPS))

```bash
sudo -i
apt-get update
apt-get install -y git curl ca-certificates python3-pip acl software-properties-common
mkdir -p /home/pfe          # the playbook and the Jenkins container use /home/pfe/.kube and /home/pfe/.minikube
```

**Python 3.12** (Ubuntu 22.04 only; on 24.04 `python3` already is 3.12). The deadsnakes PPA [112] packages newer Pythons for Ubuntu LTS releases:

```bash
add-apt-repository -y ppa:deadsnakes/ppa
apt-get install -y python3.12 python3.12-venv python3.12-dev
python3.12 --version                     # Python 3.12.x
```

**Ansible.** The `community.docker` collection the playbook uses does not support ansible-core older than 2.17 [113], and Ubuntu 22.04's apt package is 2.10. Install ansible-core and the Docker SDK for Python with pip instead (pinned to the versions the VPS ran):

```bash
apt-get remove -y ansible 2>/dev/null || true
# Ubuntu 22.04:
pip3 install "ansible-core==2.17.14" docker==7.1.0
# Ubuntu 24.04 refuses plain pip installs; use this line instead:
# pip3 install --break-system-packages "ansible-core==2.17.14" docker==7.1.0
ansible --version | head -1                                              # ansible [core 2.17.14]
python3 -c "import docker; print('docker SDK', docker.__version__)"     # docker SDK 7.1.0
```

On a machine with a desktop (the VPS was used over RDP), **do not run `apt autoremove`** after removing the apt `ansible` package: it offers to remove packages the desktop session needs.

The playbook itself now stops at once, with this instruction, if ansible-core is older than 2.17 (`site.yml`, first pre-task; FIXED, re-test).

Clone the repository and install the collection:

```bash
git clone https://github.com/aminshil/devsecmlops-pfe.git /root/devsecmlops-pfe
cd /root/devsecmlops-pfe
ansible-galaxy collection install -r ansible/requirements.yml
ansible-galaxy collection list community.docker | tail -2
```

**SonarQube's kernel setting.** SonarQube's Elasticsearch refuses to start unless `vm.max_map_count` is at least 524288 [114]; Ubuntu's default is 65530. The playbook now writes `/etc/sysctl.d/99-sonarqube.conf` and applies it (`prerequisites` role; FIXED, re-test). By hand, as on the VPS:

```bash
sysctl -w vm.max_map_count=524288 && echo 'vm.max_map_count=524288' > /etc/sysctl.d/99-sonarqube.conf
```

The repository path is free: the playbook derives it from its own location. `/home/pfe` is the only fixed path.

---

## 2. Python environment and tests  (VERIFIED (VPS))

```bash
cd /root/devsecmlops-pfe
python3.12 -m venv venv && source venv/bin/activate      # on 24.04: python3 -m venv venv
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest -q tests          # expect: 275 passed
```

The VPS ran **272 passed** at commit `a280267a`; the commit that added this guide (`1b591925`) adds 3 tests, and the VPS's Jenkins build #2 ran all **275** on it. If the count does not match, stop here.

The playbook reuses this virtualenv. It now checks the venv's Python before installing anything and stops with the deadsnakes instruction if it is older than 3.12 (`app_setup` role; FIXED, re-test). To let the playbook create the venv itself, run it with `-e venv_python=python3.12`.

---

## 3. The four datasets  (VERIFIED on two more machines)

The datasets are not in git (`data/*.csv` is ignored). They come from the project's own generator, `ml-model/generate_telecom_fleet.py`. Each fleet has its own seed, so the files share no rows; they do describe the same simulated fleet (same machine names and calendar) with independent noise, which is why a held-out score here is optimistic compared with a genuinely new site.

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

**Reproducibility.** The training run records the SHA-256 of the training and test files in `models/manifest.json`:

```text
train  b4a8080965f4f0bc12b2727916969c6fe728a4dcb78bb169616155bba16e43cf
test   3b6b6e2144100d43522a79a9a685eecc49e2bcecc573b001584614f0a5a8894a
val    ea0a20c29f8d3e34a77c8db79abc43ccd0d051d5049003cda659e0194c1148a0
```

```bash
sha256sum data/telecom_fleet_v2_labeled.csv data/telecom_fleet_v2_test.csv data/telecom_fleet_v2_val.csv
```

These files have now been regenerated, from scratch, on two machines other than the original VM, and the training and test fleets came out **byte for byte identical** both times:

| Machine | Environment | Result |
|---|---|---|
| Second environment | Ubuntu 24.04, Python 3.13, numpy 2.5.3, pandas 2.3.3, 2 CPUs, 7.8 GB | train, val (14 days) and test: the three hashes above |
| The VPS | Ubuntu 22.04.5, Python 3.12, numpy 2.5.4 | train and test: the hashes above. Its validation and operator fleets were generated with `--days 30` (a choice made for that test), so their hashes differ by construction and were not compared |

The operator fleet (`--seed 2026`) has no recorded hash. If your hashes differ anyway (another numpy or pandas version changes the random stream), the files are still valid datasets from the same generator; the model job records the new hashes and the scores will differ slightly from the README.

---

## 4. Credentials  (VERIFIED (VPS))

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

Never paste the contents of this file into a chat, a ticket or git.

**Production profile: the database password in an Ansible Vault.** Two lessons from the VPS:

- **No URL characters in the database password** (`@ : / ? # %`). The API's `DATABASE_URL` is assembled from the Secret's values without URL-encoding (`ansible/roles/kubernetes/templates/deployment.yaml.j2`), so an `@` in the password breaks the address. `scripts/make_production_secrets.sh` generates a random password that avoids them.
- **Keep the Vault password in a root-only file.** Typed at a hidden prompt through RDP, the Vault password came out different from the one intended (the keyboard layout changed a character), and decryption failed. A file avoids it:

```bash
install -m 600 /dev/null /root/.vault_pass && nano /root/.vault_pass        # one line: the Vault password
ANSIBLE_VAULT_PASSWORD_FILE=/root/.vault_pass scripts/make_production_secrets.sh
ansible-vault view --vault-password-file /root/.vault_pass ansible/group_vars/production_secrets.yml | sed 's/:.*/: (hidden)/'
```

`ansible/group_vars/production_secrets.yml` is in `.gitignore`: it is encrypted, but it is never committed. The demo profile generates a random database password on its own and needs no Vault.

---

## 5. Run Ansible

```bash
cd /root/devsecmlops-pfe/ansible
ansible-playbook -i inventory.ini site.yml --syntax-check
```

Optional dry run (changes nothing; the bucket task and all installs are skipped). Use throwaway values of 12+ characters, **never run the real playbook with them**: MinIO would be recreated with the wrong credentials.

```bash
MINIO_ROOT_USER=x MINIO_ROOT_PASSWORD=placeholder-12345 GRAFANA_ADMIN_PASSWORD=placeholder-12345 OPS_UI_USER=x OPS_UI_PASSWORD=placeholder-12345 ansible-playbook -i inventory.ini site.yml --check
```

### 5a. The MinIO image  (VERIFIED (VPS), manual, demo profile only)

`ansible/group_vars/all.yml` pins MinIO by digest (`img_minio`), as it was running on the original VM. That image can no longer be pulled: on 23 October 2025 MinIO stopped publishing its free community images, removed the existing ones from Docker Hub and Quay, and moved the community edition to source-only distribution [115] (the VPS got `pull access denied` from Docker Hub and `401 UNAUTHORIZED` from Quay). The digest was pinned on 27 October 2026 from the original VM's local cache, so it has never been pullable from a registry since it was written. The original VM still runs it from its local cache, which is why nothing broke there.

On a new machine, build a local image from the **same release**, `RELEASE.2025-09-07T16-13-09Z`, using MinIO's own binary from its GitHub release [116], checked against the release's own `.sha256sum` file and against the SHA-256 the VPS got:

```bash
mkdir -p /root/minio-img && cd /root/minio-img
R=RELEASE.2025-09-07T16-13-09Z
curl -fsSL -o minio          "https://github.com/minio/minio/releases/download/$R/minio.linux-amd64.$R"
curl -fsSL -o minio.sha256sum "https://github.com/minio/minio/releases/download/$R/minio.linux-amd64.$R.sha256sum"
echo "$(cut -d' ' -f1 minio.sha256sum)  minio" | sha256sum -c -                       # minio: OK
echo "7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f  minio" | sha256sum -c -   # the VPS's value
printf 'FROM debian:12-slim\nCOPY minio /usr/bin/minio\nRUN chmod +x /usr/bin/minio\nEXPOSE 9000 9001\nENTRYPOINT ["minio"]\n' > Dockerfile
docker build -t minio-pinned:local . && docker run --rm minio-pinned:local --version | head -1
```

Expected: `minio version RELEASE.2025-09-07T16-13-09Z` with `commit-id=07c3a429bfed433e49018cb0f78a52145d4bedeb`, the same commit as the image on the original VM. Then add `-e img_minio=minio-pinned:local` to every demo-profile run below. The production profile does not run MinIO.

### 5b. Production profile  (VERIFIED (VPS))

```bash
cd /root/devsecmlops-pfe/ansible && source /root/.devsecmlops.env
ansible-playbook -i inventory.ini site.yml -e profile=production --vault-password-file /root/.vault_pass 2>&1 | tail -30
```

It installs Docker, kubectl, Minikube and Prometheus (pinned; skipped when present), checks the venv, builds the API image, starts Minikube, applies the Vault-backed database Secret, deploys PostgreSQL and **3 API replicas** with resource limits, and starts the monitoring units. It skips Jenkins, SonarQube, MinIO, MLflow and the control panel. On the VPS: `failed=0`, 3 replicas, limits `cpu 1 / 512Mi`, HPA 2–5, `db user: anomaly` read from the Vault.

On the VPS, Minikube had to be started by hand before this run (`minikube start --driver=docker --force && minikube addons enable metrics-server`), because the `secrets` role runs before the `kubernetes` role and needs the cluster. The `secrets` role now starts the cluster first (`roles/kubernetes/tasks/cluster.yml`; FIXED, re-test).

### 5c. Demo profile  (VERIFIED (VPS); one pass now)

```bash
cd /root/devsecmlops-pfe/ansible && source /root/.devsecmlops.env
ansible-playbook -i inventory.ini site.yml -e img_minio=minio-pinned:local 2>&1 | tail -40      # demo is the default profile
```

This adds the registry, MinIO (and its `mlflow-artifacts` bucket, via `scripts/ensure_minio_bucket.py`), MLflow, SonarQube, the custom Jenkins image, Grafana, node-exporter, the systemd units (Prometheus, k8s-exporter, production agent, control panel) and the Jenkins network unit. On the VPS, on top of the production run: `failed=0, changed=20`.

What the VPS run needed by hand, now done by the playbook (all FIXED, re-test):

| Was needed by hand on the VPS | Now |
|---|---|
| `docker network create devsecmlops-net` (the registry task stopped on "network not found") | `prerequisites` creates it first |
| Minikube started before the playbook: the old guide ran a first pass with `--tags system,app_setup,kubernetes`, because `prerequisites` attaches Jenkins to the Docker network `minikube`, which only Minikube must create | `prerequisites` starts the cluster before that step; a single run is enough |
| `-e @group_vars/<profile>.yml` with `--tags`: the pre-tasks that define `profile` were skipped by `--tags` | the pre-tasks are tagged `always`, which runs under any `--tags` [118] |
| `KUBECONFIG` and `MINIKUBE_HOME` exported in the shell, or minikube as root used `/root/.minikube`, a cluster Jenkins cannot see | the play exports both for every task |
| Jenkins given access to the cluster files with `setfacl` (stages 8–9 need them) | the `kubernetes` cluster tasks grant the ACL to the Jenkins uid only (demo profile) |
| The Jenkins image's Docker group id: fixed at 999 | built with the host socket's group id; the play checks that Jenkins can reach Docker |
| A ufw rule so the Jenkins model job can reach MLflow (section 7) | `prerequisites` adds it when ufw is active |

**Re-run after the fixes** (same VPS, 2 November, in a shell with no `KUBECONFIG`/`MINIKUBE_HOME` exported): `--tags kubernetes` alone, `failed=0` (no `'profile' is undefined`); the full demo run, `failed=0`, the network and ufw tasks `ok` (already in place), the sysctl file and the ACL applied once, the Jenkins→Docker check `ok`, and `11/11 layers up`. The Jenkins image built with `DOCKER_GID` from the host socket: `groups=1000(jenkins),999(dockerhost)`.

### 5d. Check it

```bash
export KUBECONFIG=/home/pfe/.kube/config MINIKUBE_HOME=/home/pfe/.minikube
systemctl is-active prometheus k8s-exporter production-agent control-panel mlflow
docker ps --format '{{.Names}}  {{.Status}}'
curl -s -o /dev/null -w "control panel /ui %{http_code}\n" http://localhost:8000/ui
curl -s http://192.168.49.2:30080/health | python3 -c "import sys,json; d=json.load(sys.stdin); l=d['model_lineage']; print(d['status'], d['version'], l.get('threshold'), '|', l.get('threshold_source'))"
```

Expected (VPS): five `active`; containers `registry minio sonarqube jenkins grafana node-exporter` plus `minikube`; `/ui` answers `200` (the control panel lives at `/ui`; `/` is a `404` by design); `/health` prints `ok`, the version, `0.91` and `models/manifest.json production.decision_threshold`. Ports seen from the VPS itself: Jenkins `8080` (403, login), SonarQube `9000`, Grafana `3000`, MLflow `5001`, MinIO console `9002`, Prometheus `9090`.

The playbook's last line now reports the control panel's layer count (`N/N layers up`); it printed `?/?` before, because its status call had no credentials (FIXED, re-test).

---

## 6. One-time Jenkins and SonarQube configuration  (MANUAL; VERIFIED (VPS) on a blank Jenkins and SonarQube)

Ansible does not configure Jenkins' jobs, credentials and plugins, nor SonarQube's project and token: they live in the Docker volumes `jenkins_home` and `sonarqube_data`. A new machine starts with both empty. These are the steps as replayed on the VPS; the names are the ones the Jenkinsfiles use.

### 6a. SonarQube  (`http://localhost:9000`)

1. Log in with `admin` / `admin` and set a new password. The password policy needs upper and lower case, a digit and a special character: `pfe-test123@`-style was refused, a capitalised one accepted.
2. Create a project, *Local project*, key **`devsecmlops-pfe`** (Jenkinsfile: `-Dsonar.projectKey=devsecmlops-pfe`).
3. Generate a token (My Account, Security) and keep it for 6b.
4. Administration, Configuration, Webhooks: create one (any name), URL **`http://jenkins:8080/sonarqube-webhook/`**, secret left empty. Without it stage 2b waits until its 3-minute timeout.
5. Quality gate: the default *Sonar way* gate. On the VPS the first analysis passed it (`Quality gate is 'OK'`).

### 6b. Jenkins  (`http://localhost:8080`)

1. Unlock: `docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword`, install the suggested plugins, create the admin user. (The admin password can be changed later under the user menu, *Security*.)
2. Plugins the Jenkinsfiles need: Pipeline (`workflow-aggregator`), Git, Credentials Binding (all three among the suggested plugins), and **SonarQube Scanner for Jenkins** (`sonar`), installed from Manage Jenkins, Plugins. On the VPS all four were then present.
3. Manage Jenkins, Credentials, add:
   - id **`github-pat-devsecmlops`**: *Username with password*, your GitHub user name and the token (section 0). Used for checkout and for pushing a promoted model.
   - a *Secret text* credential holding the SonarQube token from 6a.3 (any id; the VPS used `sona-token`).
4. Manage Jenkins, System, SonarQube servers: tick *Environment variables*, add one named **`sonarqube`**, URL **`http://sonarqube:9000`**, token = the Secret text above. Not `localhost`: inside the Jenkins container `localhost` is Jenkins itself; the two containers reach each other by name over `devsecmlops-net`.
5. Create two Pipeline jobs, each *Pipeline script from SCM*, Git, `https://github.com/aminshil/devsecmlops-pfe.git`, branch `*/main`, credential `github-pat-devsecmlops`:
   - **`devsecmlops-pipeline`**, script path `Jenkinsfile`
   - **`devsecmlops-model`**, script path `Jenkinsfile.model` (its parameters `SCOPE`, `INCLUDE_FEEDBACK`, `PROMOTE` appear after the first run; *Build with Parameters* then replaces *Build Now*)

Check from the host that Jenkins has its tools and reaches its neighbours:

```bash
docker exec jenkins bash -c 'python3 --version; sonar-scanner --version | head -1; trivy --version | head -1; kubectl version --client | head -1; docker ps -q | wc -l'
docker exec jenkins curl -s http://sonarqube:9000/api/system/status; echo
docker exec jenkins kubectl --kubeconfig /home/pfe/.kube/config -n ml-serving get pods
```

VPS: Python 3.13.5, Trivy 0.74.0, kubectl v1.36.2; SonarQube `UP`; the API pods and `postgres-0` listed.

---

## 7. First builds and final check  (VERIFIED (VPS))

1. **`devsecmlops-pipeline`**, Build Now. SonarQube must be running. On the VPS, build #1 took about 8 minutes, all 12 stages green: `ruff` clean, 272 tests, Quality Gate OK, repository scan clean, model gate F1 0.7292, image built, smoke-tested (7 PASS), 0 HIGH/CRITICAL vulnerabilities, pushed, deployed by a rolling update and smoke-tested again inside the new pod. The first Trivy run downloads its vulnerability database (about 131 MB). If the post-deploy smoke test fails, the pipeline rolls the deployment back (`kubectl rollout undo`). Build #2, on the fixed commit `1b591925` (2 November): `ruff` clean, 275 tests, Quality Gate OK on the changed code, 0 HIGH/CRITICAL vulnerabilities, `2.20.3-b2` pushed, deployed and smoke-tested in the new pod.
2. **`devsecmlops-model`**, `SCOPE=smoke` (`INCLUDE_FEEDBACK` on, `PROMOTE` off). A few minutes. On the VPS it ended **UNSTABLE, which is the correct result**: the candidate trained on the 3-day smoke fleet (v4 F1 0.5537) was rejected by the guardrail against production (0.7731 on the same data), and all 17 production files were hash-checked unchanged ("the real models/ directory is untouched").
3. **`devsecmlops-model`**, `SCOPE=full`, **`PROMOTE` off**. About 26 minutes on the VPS: 17,280,000 training rows plus the operator verdicts exported from PostgreSQL (28 rows), candidate trained in 892 s, candidate v4 F1 0.7290 against production 0.7292, guardrail PASS, run logged to MLflow with its six files stored in MinIO. Keep `PROMOTE` off on a test machine: on, stage 7 commits the model and pushes it to `main`.
   - **The firewall.** The model job reaches MLflow on the host at `http://172.17.0.1:5001`, through Docker's default bridge. On the VPS ufw was active with a default-deny policy, so the job stopped at "MLflow is not reachable" (the log showed `[UFW BLOCK] IN=docker0 SRC=172.17.0.2 DST=172.17.0.1 DPT=5001`). Docker's own documentation notes that Docker and ufw filter traffic in ways that interact badly [117]. The rule that fixed it opens port 5001 to that bridge only, nothing on the public side (the `prerequisites` role now adds it when ufw is active; FIXED, re-test):
     ```bash
     ufw allow from 172.17.0.0/16 to 172.17.0.1 port 5001 proto tcp comment 'Jenkins container -> MLflow'
     docker exec jenkins curl -s -o /dev/null -w "%{http_code}\n" http://172.17.0.1:5001/health      # 200
     ```
   - **Stage 6 (MLflow and MinIO read-back).** On the VPS this stage failed with "no artifact hashes": the candidate's file hashes were recorded only with `PROMOTE` on, so a full run without promotion could never pass it. The same check, run by hand on that run, found all 6 files identical in MinIO. `train_production.py` now records them on every run (`candidate_artifacts`) and `scripts/verify_mlflow_artifacts.py` reads them. **Confirmed** by the full run on `1b591925` (2 November): candidate trained in 808 s, the same F1 values (0.7290 against 0.7292), guardrail PASS, stage 6 read the 6 hashes from `candidate_artifacts`, downloaded the 6 files back from MLflow (MinIO), found all 6 identical, and the job ended `Finished: SUCCESS` (MLflow run `8c25418d…`).
4. **Alerting (Telegram), optional.** Create a bot with @BotFather, then `bash scripts/setup_alerting.sh` (details in section 8). On the VPS the Telegram list and Grafana's "Anomalous machines now" table named the same 8 machines when compared, and all 200 machines were reporting on time.
5. **Checklist:**

```bash
cd /root/devsecmlops-pfe && source venv/bin/activate
export KUBECONFIG=/home/pfe/.kube/config MINIKUBE_HOME=/home/pfe/.minikube
python3 scripts/verify_model_manifest.py --min-f1 0.60      # the CI model gate: artifact hashes, F1, threshold
python3 scripts/live_k8s_validation.py --days 14 --every-hours 4 --out build/live_validation.json 2>&1 | tail -15   # about 5 minutes
bash scripts/full_verification.sh && cat "$(ls -t /tmp/full_verification_*.txt | head -1)"
```

VPS: the live validation gave exactly the original VM's numbers, 16,600 requests, 0 errors, F1 0.7731, precision 0.7166, recall 0.8391, cause accuracy 0.9977 (latency p50 395 ms, p95 588 ms), on the production profile's 3 replicas. `full_verification.sh` found every layer healthy (12 of 12 Prometheus targets up); it now exports the cluster paths itself and checks the production agent, Alertmanager, MLflow and MinIO instead of the retired fleet simulator (FIXED, re-test).

---

## 8. Day-to-day  (VERIFIED (VM))

| Task | Command |
|---|---|
| Survive a reboot | Automatic: the host services are systemd units and the containers restart on their own. `jenkins-networks.service` re-attaches Jenkins to its networks |
| Free memory for a full model run | Only below 9,000 MiB free: stop SonarQube, Grafana, node-exporter, production-agent, k8s-exporter, Prometheus. Keep Jenkins, MinIO, MLflow, Kubernetes |
| Disk | `df -h /` before a demo; prune old images (`docker image prune -a` after checking what you need). SonarQube goes read-only at 95% |
| Change training size or the recall target | Edit **only** `config/model_policy.json` |
| Rotate credentials | Change the values in `/root/.devsecmlops.env`, re-run the playbook (this rewrites the root-only files and restarts the units), then change Grafana's and SonarQube's passwords in their UIs |
| Run `kubectl` or `minikube` as root by hand | `export KUBECONFIG=/home/pfe/.kube/config MINIKUBE_HOME=/home/pfe/.minikube` first |
| Rebuild the venv on Python 3.12 (gap 15: a venv made with Python 3.10 before numpy 2.5.4 was pinned) | See below. A procedure, **not yet run** on the original VM |

**Rebuilding the venv on Python 3.12** (gap 15). The services that run from the venv are stopped for a few minutes while it is rebuilt; the old venv is kept, so going back is one `mv`. Not yet run on the original VM: do it when no demo is due.

```bash
add-apt-repository -y ppa:deadsnakes/ppa && apt-get install -y python3.12 python3.12-venv python3.12-dev
cd /root/devsecmlops-pfe
U=$(grep -l "$PWD/venv/bin" /etc/systemd/system/*.service | xargs -n1 basename); echo "units using the venv: $U"
systemctl stop $U
mv venv venv-py310 && python3.12 -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-dev.txt
venv/bin/python --version && venv/bin/python -m pytest -q tests 2>&1 | tail -1      # Python 3.12.x, 275 passed
systemctl start $U && systemctl is-active $U
# to go back: systemctl stop $U && mv venv venv-py312 && mv venv-py310 venv && systemctl start $U
```

### Optional hardening (written, apply by hand; not part of the default run)

**Alerting (Telegram)** (VERIFIED (VPS)). Create a bot with @BotFather, then on the machine: `sudo bash scripts/setup_alerting.sh`. It asks for the token once (not echoed, stored root-only), finds the chat id from a message you send to the bot, validates the rules (`scripts/check_alerting.sh`) before installing anything, starts Alertmanager (127.0.0.1:9093, this host only) and the blackbox exporter (:9115), restarts Prometheus, installs the `prometheus-watchdog` service (Prometheus or Alertmanager down → Telegram) and sends a test alert. Re-send a test alert any time with `sudo bash scripts/setup_alerting.sh --test`. To pause the messages, use the Notifications card on the control panel's Infrastructure tab (mute the anomaly list or everything, for a set time or until unmuted). ufw does not get in the way here: Prometheus, Alertmanager and the probes all use the host network and `localhost`.

**NetworkPolicy check.** `kubernetes/networkpolicy.yaml` lets only the API pods reach PostgreSQL. It takes effect only if the cluster's network plugin enforces policies, so prove it:

```bash
kubectl apply -f kubernetes/networkpolicy.yaml
kubectl -n ml-serving run np-test --rm -i --restart=Never --image=postgres:16-alpine -- pg_isready -h postgres -t 5    # expect: no response (blocked)
kubectl -n ml-serving exec deploy/anomaly-api -c api -- python3 -c "import socket;socket.create_connection(('postgres',5432),3);print('api reaches postgres')"   # expect: api reaches postgres
```

If the first command still answers "accepting connections", the plugin does not enforce policies: the file does nothing, so delete it (`kubectl delete -f kubernetes/networkpolicy.yaml`) and do not claim it in the defense.

**Feedback token:** `export FEEDBACK_TOKEN=...` before the playbook creates the `feedback-token` Secret and gives the control panel the same value; without it `/feedback` stays open. **CI settings as files:** `python scripts/ci_config.py export` (read-only) writes `ci-config/` from the running Jenkins and SonarQube; `restore --yes` replays the jobs. Both need `JENKINS_USER`, `JENKINS_TOKEN` and `SONAR_TOKEN`; the export script is tested against a fake server only.

---

## 9. Gaps found on the clean machine (VPS, 31 October – 1 November 2026; gap 15 on the original VM)

| # | Gap | What happened on the VPS | State |
|---|---|---|---|
| 1 | Python 3.12+ needed | Ubuntu 22.04's Python 3.10 cannot install numpy 2.5.4 | Guide: deadsnakes (section 1). Playbook: stops early with the instruction (FIXED, re-test) |
| 2 | ansible-core 2.17+ needed | apt's Ansible 2.10.8 refused by `community.docker` | Guide: pip install (section 1). Playbook: stops early with the instruction (FIXED, re-test) |
| 3 | `--tags` skipped the pre-tasks | `'profile' is undefined` | Pre-tasks tagged `always` (FIXED, re-test) |
| 4 | Cluster paths and start order | minikube as root used `/root/.minikube`; the production `secrets` role ran before Minikube had started | The play exports `KUBECONFIG`/`MINIKUBE_HOME`; `secrets` and `prerequisites` start the cluster first (FIXED, re-test) |
| 5 | `devsecmlops-net` never created | the registry task stopped | Created by `prerequisites` (FIXED, re-test) |
| 6 | Jenkins and the cluster files; Docker group id 999 hard-coded | ACL granted by hand | ACL for the Jenkins uid in `cluster.yml`; the image takes the socket's group id (`DOCKER_GID`), and the play checks Jenkins can reach Docker (FIXED, re-test) |
| 7 | `vm.max_map_count` | 65530, too low for SonarQube | Set and persisted by `prerequisites` (FIXED, re-test) |
| 8 | MinIO image deleted upstream | `pull access denied` (Docker Hub), `401` (Quay) | Guide: a local image from the official binary, same release (section 5a). Repository: **open**, a decision is needed (mirror the image into the local registry, or move to a maintained image) |
| 9 | The control panel summary printed `?/?` | its status call had no credentials | Basic auth on that call (FIXED, re-test) |
| 10 | The guide checked `:8000/` | `404` by design | Guide checks `/ui` (section 5d) |
| 11 | ufw blocked the model job → MLflow | `[UFW BLOCK] … DPT=5001` | Rule added by `prerequisites` when ufw is active (FIXED, re-test) |
| 12 | Model job stage 6 failed without `PROMOTE` | "no artifact hashes" | Hashes recorded on every run; 3 tests. **Confirmed**: the full run on `1b591925` (2 November) passed stage 6 by itself, `Finished: SUCCESS` |
| 13 | node-exporter series labelled `machine="pfe-vm"` | cosmetic: the label is fixed in `monitoring/prometheus.yml` | Open, cosmetic |
| 14 | `full_verification.sh` outdated | it counted the retired fleet simulator (always 0) and needed the cluster paths exported | Updated (FIXED, re-test: run on the VPS with no paths exported, every check `200`/`active`, 200 machines reporting) |
| 15 | The original VM's venv is Python 3.10 (numpy 2.2.6) | Found on the original VM after the VPS run (2 November), not on the VPS: `requirements-api.txt` pins numpy 2.5.4 since 27 October (commit `8dced809`), which cannot install on 3.10, so a playbook re-run on the VM stops at the venv check (before that check existed it stopped at the pip step). Nothing that runs is affected: Jenkins tests on Python 3.13 and the image serves on 3.12 | Open: rebuild the VM's venv on Python 3.12 when convenient (section 8; procedure not yet run there) |

Still true from before:

| Gap | State |
|---|---|
| Jenkins jobs, credentials, plugins and the SonarQube project are not codified | MANUAL (section 6), replayed on the VPS. Jenkins Configuration as Code would remove this; not done |
| `/home/pfe` is a fixed path | Create it, or change `kubeconfig_path`, `minikube_home` and the Jenkins volume mounts together |
| A new `docker` group membership does not apply to a running play | The play uses a local connection and cannot refresh it. On a non-root account, log out and in (or `newgrp docker`) after the first Docker install and re-run |
| The served model's MLflow run lives on the original VM | `/health` names MLflow run `d81b7ea6…`, logged on the VM on 21 October; a new machine's MLflow does not have it, so `scripts/verify_mlflow_artifacts.py` (default mode) only works there. A new machine's own runs are verified with `--report` (section 7) |

Sources [111]–[118] are listed in `docs/SOURCES.md`.
