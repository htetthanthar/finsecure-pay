# Jenkins controller with every CLI the FinSecure Pay pipeline needs.
# Doubles as an operations toolbox: `docker compose exec jenkins bash`
# gives you aws, eksctl, kubectl, helm and kustomize without installing
# anything on Windows.
FROM jenkins/jenkins:lts-jdk21

ARG KUBECTL_MINOR=1.36
ARG KUSTOMIZE_VERSION=5.7.1
ARG GITLEAKS_VERSION=8.28.0
ARG SONAR_SCANNER_VERSION=8.1.0.6389

USER root

# Fail any RUN step whose pipe fails (e.g. `curl ... | bash` with a failed download).
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Base tools + Docker CLI (talks to the host's Docker through the mounted socket).
# Packages are left unpinned on purpose so each rebuild picks up security patches.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg unzip jq git make python3 python3-yaml \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list \
 && curl -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key | gpg --dearmor -o /etc/apt/keyrings/trivy.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb generic main" \
      > /etc/apt/sources.list.d/trivy.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin trivy \
 && rm -rf /var/lib/apt/lists/*

# Refuse Trivy releases compromised in the March 2026 supply-chain attack (CVE-2026-33634).
RUN TRIVY_VERSION_INFO="$(trivy --version)" \
 && echo "$TRIVY_VERSION_INFO" \
 && if grep -Exq 'Version: 0\.69\.(4|5|6)' <<< "$TRIVY_VERSION_INFO"; then \
      echo "Refusing compromised Trivy release" >&2; exit 1; \
    fi

# kubectl: latest patch of the cluster's minor version (client must be within +/-1 minor)
RUN KVER=$(curl -fsSL "https://dl.k8s.io/release/stable-${KUBECTL_MINOR}.txt") \
 && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KVER}/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl

# kustomize, helm, eksctl, gitleaks
RUN curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
      | tar -xz -C /usr/local/bin kustomize \
 && curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash \
 && curl -fsSL "https://github.com/eksctl-io/eksctl/releases/latest/download/eksctl_Linux_amd64.tar.gz" \
      | tar -xz -C /usr/local/bin eksctl \
 && curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
      | tar -xz -C /usr/local/bin gitleaks

# AWS CLI v2
RUN curl -fsSLo /tmp/awscliv2.zip https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip \
 && unzip -q /tmp/awscliv2.zip -d /tmp \
 && /tmp/aws/install \
 && rm -rf /tmp/aws /tmp/awscliv2.zip

# SonarScanner CLI 8.x (generic zip, runs on the image's Java 21)
RUN curl -fsSLo /tmp/sonar.zip "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}.zip" \
 && unzip -q /tmp/sonar.zip -d /tmp/sonar \
 && mv /tmp/sonar/sonar-scanner-* /opt/sonar-scanner \
 && ln -s /opt/sonar-scanner/bin/sonar-scanner /usr/local/bin/sonar-scanner \
 && rm -rf /tmp/sonar.zip /tmp/sonar

# uv + Python 3.11 (same minor version as the distroless runtime image)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.11 && chmod -R a+rX /opt/uv-python

USER jenkins

# Plugins and configuration-as-code
COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
# Kept outside /var/jenkins_home (a volume), so a rebuilt image always applies the
# current casc.yaml instead of the copy frozen into the volume on first start.
COPY --chown=jenkins:jenkins casc.yaml /var/jenkins_conf/casc.yaml
ENV CASC_JENKINS_CONFIG=/var/jenkins_conf/casc.yaml \
    JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
