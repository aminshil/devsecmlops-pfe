# =============================================================================
# MLflow tracking server (the Jenkins model job and the training scripts log to it).
#
# It used to run on the host as a systemd unit from the project's virtualenv, listening on every
# interface without a login so that the Jenkins container could reach it through the Docker bridge.
# As a container on devsecmlops-net, Jenkins reaches it by name (http://mlflow:5001) like SonarQube,
# the registry and MinIO, and the host publishes it on 127.0.0.1 only.
#
# Built by the Ansible prerequisites role (devsecmlops-mlflow:<mlflow_version>). The base image is
# pinned by digest and MLflow and boto3 by version, so a rebuild gets the same server. MLflow's version
# is the one in requirements.txt (the clients that talk to it use the same one).
# boto3 is what lets the server write artifacts to MinIO (--serve-artifacts).
# Runs as the unprivileged uid 10003; /data (the SQLite file) is the mlflow_data volume.
# =============================================================================
ARG MLFLOW_BASE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
FROM ${MLFLOW_BASE}

ARG MLFLOW_VERSION=3.17.2
ARG BOTO3_VERSION=1.44.0
LABEL org.opencontainers.image.title="mlflow" \
      org.opencontainers.image.version="${MLFLOW_VERSION}"

RUN pip install --no-cache-dir "mlflow==${MLFLOW_VERSION}" "boto3==${BOTO3_VERSION}" \
    && useradd --system --uid 10003 --home-dir /data --shell /usr/sbin/nologin mlflow \
    && mkdir -p /data && chown 10003:10003 /data

USER 10003:10003
WORKDIR /data
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/health', timeout=4)"
ENTRYPOINT ["mlflow", "server"]
