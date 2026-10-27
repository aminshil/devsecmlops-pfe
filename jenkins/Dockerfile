# =============================================================================
# Custom Jenkins agent image for the DevSecMLOps pipeline.
#
# The stock jenkins/jenkins:lts image only bundles Java. Every other tool
# the Jenkinsfile calls -- python3 (tests), sonar-scanner (SAST), the docker
# CLI (build/push/deploy), trivy (CVE scan), kubectl and the minikube CLI
# (the automated K8s deploy stage) -- was, for most of this project's
# history, installed live into the running container by hand and never
# captured in source control. That state was invisible to anyone
# provisioning a fresh VM from the Ansible playbook, and vanished on every
# container recreation. This Dockerfile is the actual, versioned source of
# truth for that toolchain.
#
# jenkins_home (jobs, credentials, build history) is a separate Docker
# volume, untouched by rebuilding this image.
#
# Pinned (external review, 27 October): the base image by digest, and every
# downloaded tool by version AND SHA-256, checked before it is installed, so a
# rebuild gets exactly these bytes or fails. The values were written by
# scripts/pin_versions.py from the official checksum files and from the images
# running on the VM; re-run it to move a pin.
# =============================================================================
ARG JENKINS_BASE=jenkins/jenkins:2.568.3-lts@sha256:c1e4c349365f6d16d88595b2c5f7e8ff39b8ae1d061f62420bac193b4b9616d0
FROM ${JENKINS_BASE}

USER root

# --- OS packages -------------------------------------------------------------
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        python3 python3-pip python3-venv \
        unzip curl gnupg ca-certificates lsb-release \
    && rm -rf /var/lib/apt/lists/*

# --- Docker CLI only (the daemon is the HOST's, reached via the mounted
#     /var/run/docker.sock -- never a second daemon inside Jenkins) ----------
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
         -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) \
         signed-by=/etc/apt/keyrings/docker.asc] \
         https://download.docker.com/linux/debian \
         $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
         > /etc/apt/sources.list.d/docker.list \
    && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# --- kubectl (pin matches the VM's own kubectl, so client/server skew stays
#     minimal against the real cluster) --------------------------------------
ARG KUBECTL_VERSION=v1.36.2
ARG KUBECTL_SHA256=1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
         -o /usr/local/bin/kubectl \
    && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod +x /usr/local/bin/kubectl

# --- minikube CLI (pin matches the VM's own minikube, since `minikube image
#     load`/status talk to a specific cluster profile format) ---------------
ARG MINIKUBE_VERSION=v1.38.1
ARG MINIKUBE_SHA256=099477eaf248bcb5bcea8ce78a2898e93ac01461c35189da1848c3de82ecd22e
RUN curl -fsSL "https://storage.googleapis.com/minikube/releases/${MINIKUBE_VERSION}/minikube-linux-amd64" \
         -o /usr/local/bin/minikube \
    && echo "${MINIKUBE_SHA256}  /usr/local/bin/minikube" | sha256sum -c - \
    && chmod +x /usr/local/bin/minikube

# --- SonarQube scanner CLI (stage 3) -----------------------------------------
ARG SONAR_SCANNER_VERSION=5.0.1.3006
ARG SONAR_SCANNER_SHA256=350dbdb517c10fcb3ce70425db95c415b313cad7296c407d416d88f3d50121f8
RUN curl -fsSL "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}-linux.zip" \
         -o /tmp/sonar-scanner.zip \
    && echo "${SONAR_SCANNER_SHA256}  /tmp/sonar-scanner.zip" | sha256sum -c - \
    && unzip -q /tmp/sonar-scanner.zip -d /opt \
    && mv "/opt/sonar-scanner-${SONAR_SCANNER_VERSION}-linux" /opt/sonar-scanner \
    && ln -sf /opt/sonar-scanner/bin/sonar-scanner /usr/local/bin/sonar-scanner \
    && rm /tmp/sonar-scanner.zip

# --- Trivy CVE scanner (stages 3 and 6) -- a pinned release tarball, checked
#     against Trivy's published checksum. The vulnerability DATABASE is not part
#     of the binary: Trivy downloads the current one on every scan, so pinning
#     the binary does not make the scan stale (the ensurepip/msgpack CVE
#     incident was a stale database, not a stale binary). It replaces
#     `curl .../main/contrib/install.sh | sh`, which ran whatever the main
#     branch held at build time. -----------------------------------------------
ARG TRIVY_VERSION=0.74.0
ARG TRIVY_SHA256=2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a
RUN curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
         -o /tmp/trivy.tar.gz \
    && echo "${TRIVY_SHA256}  /tmp/trivy.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/trivy.tar.gz -C /usr/local/bin trivy \
    && rm /tmp/trivy.tar.gz

# --- let the jenkins user (uid 1000) reach the mounted docker.sock; its GID
#     must match the socket's actual host GID (999 on this VM) -- see the
#     "why do we do that now" incident: usermod alone with a mismatched GID
#     silently doesn't grant access -----------------------------------------
RUN groupadd -f -g 999 dockerhost && usermod -aG dockerhost jenkins

USER jenkins

# Sanity check baked into the image build itself
RUN python3 --version && kubectl version --client && minikube version \
    && sonar-scanner --version && docker --version && trivy --version
