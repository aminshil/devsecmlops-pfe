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
# =============================================================================
FROM jenkins/jenkins:lts

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
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
         -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl

# --- minikube CLI (pin matches the VM's own minikube, since `minikube image
#     load`/status talk to a specific cluster profile format) ---------------
ARG MINIKUBE_VERSION=v1.38.1
RUN curl -fsSL "https://storage.googleapis.com/minikube/releases/${MINIKUBE_VERSION}/minikube-linux-amd64" \
         -o /usr/local/bin/minikube \
    && chmod +x /usr/local/bin/minikube

# --- SonarQube scanner CLI (stage 3) -----------------------------------------
ARG SONAR_SCANNER_VERSION=5.0.1.3006
RUN curl -sL "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}-linux.zip" \
         -o /tmp/sonar-scanner.zip \
    && unzip -q /tmp/sonar-scanner.zip -d /opt \
    && mv "/opt/sonar-scanner-${SONAR_SCANNER_VERSION}-linux" /opt/sonar-scanner \
    && ln -sf /opt/sonar-scanner/bin/sonar-scanner /usr/local/bin/sonar-scanner \
    && rm /tmp/sonar-scanner.zip

# --- Trivy CVE scanner (stage 5) -- official install script resolves the
#     current release itself, so this never goes stale the way a pinned
#     version number would (see the ensurepip/msgpack CVE incident) --------
RUN curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
       | sh -s -- -b /usr/local/bin

# --- let the jenkins user (uid 1000) reach the mounted docker.sock; its GID
#     must match the socket's actual host GID (999 on this VM) -- see the
#     "why do we do that now" incident: usermod alone with a mismatched GID
#     silently doesn't grant access -----------------------------------------
RUN groupadd -f -g 999 dockerhost && usermod -aG dockerhost jenkins

USER jenkins

# Sanity check baked into the image build itself
RUN python3 --version && kubectl version --client && minikube version \
    && sonar-scanner --version && docker --version && trivy --version
