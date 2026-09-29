# Troubleshooting Runbook

## "Every service is unreachable" — Jenkins, SonarQube, MinIO, etc. all timeout

**Symptom:** `docker ps` shows all containers as `Up`, ports are listed
correctly, `docker-proxy` is listening (`ss -tlnp` shows it), iptables NAT
rules look correct — but `curl localhost:<port>` on every single service
times out. `curl -v` shows the TCP handshake succeeds ("Connected to
localhost port X") but zero bytes ever come back — the connection is
accepted but the request goes nowhere.

**Root cause:** Docker's internal networking (bridge, veth pairs,
docker-proxy's forwarding path to each container's network namespace) gets
into a stale state after network-disruptive events: a VM reboot, repeated
`docker stop`/`start` cycles under memory pressure, or an OOM cascade.
The container itself is healthy; only the host-to-container network path
is broken. This matches the same failure class already documented in the
Kubernetes section of the main README (Minikube's networking going stale
after a Docker daemon restart) — same underlying mechanism, different
symptom surface.

**Fix — restart the Docker daemon itself, not individual containers:**

```bash
sudo systemctl restart docker
sleep 10
docker ps --format "table {{.Names}}\t{{.Status}}"
```

Containers with `--restart=always` (all of ours: minio, sonarqube, jenkins,
registry) come back up automatically. Give them ~15-20s to fully
initialize, then verify:

```bash
sleep 15
curl -s -o /dev/null -w "Jenkins: %{http_code}\n"   -m 5 http://localhost:8080   # 403 = healthy (login wall)
curl -s -o /dev/null -w "SonarQube: %{http_code}\n" -m 5 http://localhost:9000   # 200
curl -s -o /dev/null -w "MinIO: %{http_code}\n"     -m 5 http://localhost:9001/minio/health/live  # 200
curl -s -o /dev/null -w "Registry: %{http_code}\n"  -m 5 http://localhost:5000/v2/_catalog         # 200
```

**Diagnostic steps that ruled out other causes** (useful if this exact fix
doesn't work next time — check these before restarting Docker blind):
1. `docker ps -a` — confirms containers are actually `Up`, not crash-looping
2. `sudo ss -tlnp | grep <port>` — confirms `docker-proxy` is listening host-side
3. `sudo iptables -t nat -L DOCKER -n` — confirms the DNAT port-forward rules exist
4. `curl -v` (not `-s`) — distinguishes "connection refused" (nothing listening)
   from "connects then times out with 0 bytes" (the stale-networking signature)
5. `sudo ufw status` — rule out an unexpectedly active host firewall

If all four of those check out clean but the request still times out, it's
this networking issue, not a container-level problem — go straight to the
daemon restart rather than debugging individual services one at a time.

---

## "Minikube API server unreachable" — `dial tcp 192.168.49.2:8443: i/o timeout`

**Symptom:** `kubectl` commands hang, then fail with
`Unable to connect to the server: dial tcp 192.168.49.2:8443: i/o timeout`
(sometimes `connect: no route to host`, or an SSH handshake failure from
`minikube` itself). `docker ps` shows the `minikube` container as `Up` for
hours, so the container is alive — but the Kubernetes API inside it is
unreachable. This recurred roughly every 30-90 minutes during long sessions,
and reliably after the VM was paused and resumed.

**Root cause:** containers-inside-a-container-inside-a-VM. Minikube (Docker
driver) runs the whole K8s cluster inside one Docker container; the pods run
as containers inside *that*. When the outer Docker daemon's networking goes
stale (VM pause/resume, memory pressure, or a `docker restart`), the network
path between the host, the minikube container, and the K8s API server inside
it drifts out of sync. The container stays "Up" but its internal API is
cut off.

**Fix (this worked reliably all session, less disruptive than a delete):**

```bash
sudo systemctl restart docker
sleep 10
minikube start --force        # DO NOT Ctrl+C the "Updating running docker
                              # container" step -- it can take 1-4 minutes
sleep 30
kubectl get pods --all-namespaces   # verify the API is actually reachable
```

After this, the K8s pods self-heal (their RESTARTS counter increments once --
that is normal, not a fault). The `--restart=always` Docker containers
(minio/sonarqube/jenkins/registry) come back on the daemon restart on their
own; **Minikube does not** and must be started manually with the command
above.

**Nuclear option (only if `minikube start --force` itself stalls):**

```bash
minikube delete
minikube start --force
# then re-apply manifests and reload the image:
kubectl apply -f kubernetes/namespace.yaml
kubectl apply -f kubernetes/postgres.yaml
kubectl apply -f kubernetes/deployment.yaml
kubectl apply -f kubernetes/service.yaml
kubectl apply -f kubernetes/hpa.yaml
minikube image load devsecmlops-api:2.13.0
```

The feedback PostgreSQL data is lost on a `minikube delete` (the PVC is
destroyed with the cluster) -- acceptable for a demo, but do not `delete` if
you need to preserve accumulated feedback.

**Permanent routine for pausing/resuming the VM (prevents most occurrences):**

```bash
# BEFORE pausing the VM:
minikube stop

# AFTER resuming the VM (or on a cold boot):
sudo systemctl restart docker
sleep 10
minikube start --force
sleep 30
kubectl get pods -n ml-serving
```

Cleanly stopping Minikube before the VM freezes avoids the stale-networking
state on resume. This is the single most effective preventive step.

**Related, when port-forwarding to PostgreSQL for local testing:**
`kubectl port-forward -n ml-serving svc/postgres 5432:5432` dies frequently
(especially when a long process runs against it, or when the cluster stalls).
Just restart it; kill stragglers first with
`pkill -f "port-forward.*postgres"` if the port is already bound.

---

## "Retrain rejected / feedback made no difference to the model"

**Symptom:** `scripts/export_feedback_dataset.py` followed by
`ml-model/train_production.py --feedback <export> --promote` runs cleanly
but the guardrail REJECTS the retrained candidate (a message starting
"GUARDRAIL REJECT"), or the retrained model's metrics are essentially
identical to the current one.

**This is expected behavior, not a bug**, when the feedback set is small.
With ~500 verdicts against millions of training rows, the feedback simply
cannot move the model measurably. The guardrail (`--max-f1-drop 0.0`,
`--max-recall-drop 0.02` by default -- zero aggregate F1 regression
allowed, at most a 2-point recall drop on any cause) correctly refusing to
promote a non-improving (or slightly-worse) model is exactly what it is
for. Meaningful improvement needs thousands of real operator verdicts
accumulated over weeks/months. See Engineering Decision #24 in the README.

**Gotcha that CAN cause a false result:** any quick evaluation script must
use the real functions in `ml-model/preprocess.py` (baseline construction,
z-scoring, and for v4, the rolling-feature computation) rather than a
simplified inline reimplementation -- skipping the per-machine per-window
baseline mis-scores the test set and produces wrong absolute F1 numbers.
The canonical, current served baseline is v4 F1=0.7246 (v3 fallback
F1=0.6496) at threshold 0.60, on the independent seed-123 test set -- if a
script reports something materially different for the current production
model, the preprocessing path is probably wrong, not the model.

---

## "MLflow UI doesn't load in the browser" (`localhost:5001`)

**Root cause (updated -- this used to be true, no longer is):** MLflow
used to run as a manually-started foreground process with no supervision,
which is exactly why this entry originally existed. As of the most recent
infrastructure pass, MLflow is a proper systemd service
(`mlflow.service`, `Restart=always`, enabled at boot), installed by
Ansible, with `--serve-artifacts` proxying uploads to MinIO -- MinIO
credentials live only in the service's own root-only env file
(`/etc/devsecmlops/mlflow.env`), never in a client's environment or
committed in this file.

**Fix -- check and restart the real service, do not hand-launch a new one:**

```bash
systemctl status mlflow
journalctl -u mlflow -n 50 --no-pager

# If it's genuinely down:
sudo systemctl restart mlflow
sleep 5
curl -s -o /dev/null -w "MLflow: %{http_code}\n" -m 5 http://localhost:5001
```

If `systemctl status mlflow` shows it was never installed at all, the
Ansible playbook has not been run against this VM -- see the Ansible (L6)
section of the README rather than starting MLflow by hand, since a
hand-started process will not be supervised and this same problem will
recur on the next reboot.

---

## "Nothing loads in my Windows browser, but curl works fine on the VM"

**Root cause:** the VM runs on VMware with its own IP on the local network
(e.g. `192.168.100.42`, check with `hostname -I`). `localhost` typed into
a browser on the Windows host refers to the **Windows host itself**, not
the VM — there is no service listening on the host's own `localhost`.

**Fix — always use the VM's real IP from the host browser, not `localhost`:**

| Service | URL from VM (`curl`) | URL from host browser |
|---|---|---|
| Jenkins | `http://localhost:8080` | `http://192.168.100.42:8080` |
| SonarQube | `http://localhost:9000` | `http://192.168.100.42:9000` |
| MinIO console | `http://localhost:9002` | `http://192.168.100.42:9002` |
| MLflow | `http://localhost:5001` | `http://192.168.100.42:5001` |
| Grafana | `http://localhost:3000` | `http://192.168.100.42:3000` |

Run `hostname -I` on the VM to confirm the current IP — it can change
across VM restarts depending on the VMware network configuration.

---

## "Jenkins pytest stage fails: pip/venv not found"

**Root cause:** the official `jenkins/jenkins:lts` Docker image is minimal
by design -- it has `python3` but no `pip`, `pip3`, `venv`, or
`ensurepip`. This is NOT the same environment as the VM itself (which
has a full Python setup) -- Jenkins builds run inside its own isolated
container.

**Fix (one-time per container, does not survive container recreation):**

```bash
docker exec -u root jenkins apt-get update
docker exec -u root jenkins apt-get install -y python3-pip
```

**Jenkinsfile pytest stage uses `python3 -m pip`, not bare `pip`/`pip3`**
-- more robust against PATH differences across environments:

```groovy
sh '''
    python3 -m pip install --quiet -r requirements-api.txt
    python3 -m pip install --quiet -r requirements-dev.txt
    python3 -m pytest tests/ -v --tb=short
'''
```

**If Jenkins' container ever gets recreated** (not just restarted --
recreated via `docker rm`+`docker run`, or a full environment rebuild),
this apt-get fix must be redone, since it wasn't baked into a custom
image. A more permanent fix would be a custom `Dockerfile` extending
`jenkins/jenkins:lts` with `python3-pip` pre-installed, built once and
used going forward -- not done here since the base image already had
the toolchain needed for SonarQube/Docker/Trivy stages, and this was
a late-discovered gap.

**Third layer (Debian 13/trixie PEP 668):** even with pip installed,
`pip install` refuses to run outside a venv by default
("externally-managed-environment"). Since the Jenkins container is
isolated and ephemeral per build, `--break-system-packages` is the
correct override here (not a virtual env, which we already ruled out
due to missing ensurepip):

```groovy
python3 -m pip install --quiet --break-system-packages -r requirements-api.txt
```
