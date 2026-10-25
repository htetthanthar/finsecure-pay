# Jenkins controller with every CLI the FinSecure Pay pipeline needs.
# Doubles as an operations toolbox: `docker compose exec jenkins bash`
# gives you aws, eksctl, kubectl, helm and kustomize without installing
# anything on Windows.
FROM jenkins/jenkins:lts-jdk21

ARG KUBECTL_MINOR=1.36
ARG KUSTOMIZE_VERSION=5.7.1
ARG GITLEAKS_VERSION=8.28.0
ARG SONAR_SCANNER_VERSION=7.2.0.5079

USER root

# Base tools + Docker CLI (talks to the host's Docker through the mounted socket)
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

# SonarScanner CLI (uses the JDK already in the image)
RUN curl -fsSLo /tmp/sonar.zip "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}.zip" \
 && unzip -q /tmp/sonar.zip -d /opt \
 && ln -s "/opt/sonar-scanner-${SONAR_SCANNER_VERSION}/bin/sonar-scanner" /usr/local/bin/sonar-scanner \
 && rm /tmp/sonar.zip

# uv + Python 3.11 (same minor version as the distroless runtime image)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.11 && chmod -R a+rX /opt/uv-python

USER jenkins

# Plugins and configuration-as-code
COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
COPY --chown=jenkins:jenkins casc.yaml /var/jenkins_home/casc_configs/casc.yaml
ENV CASC_JENKINS_CONFIG=/var/jenkins_home/casc_configs/casc.yaml \
    JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
