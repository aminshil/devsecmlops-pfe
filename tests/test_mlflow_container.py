"""MLflow runs as a container on devsecmlops-net (mlflow/Dockerfile, the prerequisites role): its image is
pinned, the host publishes it on 127.0.0.1 only, Jenkins reaches it by name, and the server accepts that name
(MLflow 3.17 answers 403 to any Host header it was not told about: tested on 3.17.2, SOURCES 125)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = (ROOT / "ansible" / "roles" / "prerequisites" / "tasks" / "main.yml").read_text()
GV = (ROOT / "ansible" / "group_vars" / "all.yml").read_text()
DF = (ROOT / "mlflow" / "Dockerfile").read_text()


def test_the_image_is_pinned_and_runs_the_version_the_clients_use():
    assert "__PIN__" not in DF, "the base image digest was not written"
    base = re.search(r"^ARG MLFLOW_BASE=(\S+)", DF, re.M).group(1)
    assert re.search(r"@sha256:[0-9a-f]{64}$", base) and re.search(r"^FROM \$\{MLFLOW_BASE\}$", DF, re.M)
    assert re.search(r"^ARG BOTO3_VERSION=\d+\.\d+\.\d+$", DF, re.M)
    ver = re.search(r"^ARG MLFLOW_VERSION=(\d+\.\d+\.\d+)$", DF, re.M).group(1)
    assert re.search(rf"^mlflow=={re.escape(ver)}\s*$", (ROOT / "requirements.txt").read_text(), re.M)
    assert f"mlflow=={ver}" in (ROOT / "Jenkinsfile.model").read_text()
    assert re.search(rf'^mlflow_version: "{re.escape(ver)}"', GV, re.M)
    uid = re.search(r"^mlflow_uid: (\d+)$", GV, re.M).group(1)
    assert f"USER {uid}:{uid}" in DF and f"--uid {uid}" in DF


def test_it_is_published_on_loopback_only_and_allows_its_own_name():
    assert 'ports: ["127.0.0.1:5001:5001"]' in TASKS
    assert not re.search(r'ports: \[[^\]]*"(0\.0\.0\.0:)?5001', TASKS)
    assert '"mlflow:5001,localhost:*,127.0.0.1:*"' in TASKS
    assert "--allowed-hosts" in TASKS and "--serve-artifacts" in TASKS
    assert "devsecmlops-net" in TASKS and "MLFLOW_S3_ENDPOINT_URL: \"http://minio:9000\"" in TASKS


def test_the_old_host_service_and_its_firewall_rule_are_gone():
    assert "venv/bin/mlflow" not in TASKS and "ufw allow" not in TASKS
    assert "state: absent" in TASKS and "/etc/systemd/system/mlflow.service" in TASKS


def test_jenkins_reaches_it_by_name():
    jf = (ROOT / "Jenkinsfile.model").read_text()
    assert 'MLFLOW_TRACKING_URI = "http://mlflow:5001"' in jf and "172.17.0.1" not in jf
