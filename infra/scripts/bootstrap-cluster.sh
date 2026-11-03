#!/usr/bin/env bash
# One-time setup of the EKS cluster and its platform add-ons.
#
# Run from WSL2/Linux/macOS, or inside the Jenkins container
# (`docker compose -f jenkins/docker-compose.yml exec jenkins bash`), which
# already has aws, eksctl, kubectl and helm installed.
#
# Required environment variables:
#   AWS_ACCOUNT_ID          12-digit AWS account ID
#   ALERT_EMAIL_TO          where alert e-mails go
#   SMTP_USERNAME           Gmail address that sends alerts
#   SMTP_PASSWORD           Gmail *App Password* (not your normal password)
# Optional:
#   SLACK_WEBHOOK_URL       Slack incoming-webhook URL (enables Slack alerts)
#   GRAFANA_ADMIN_PASSWORD  generated randomly if unset
#   SKIP_CLUSTER_CREATE=1   reuse an existing cluster
set -euo pipefail

cd "$(dirname "$0")/../.."
CLUSTER=finsecure-eks
REGION=ap-southeast-1
RENDER=infra/scripts/render.sh

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

for tool in aws eksctl kubectl helm python3; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done
: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}" "${ALERT_EMAIL_TO:?set ALERT_EMAIL_TO}"
: "${SMTP_USERNAME:?set SMTP_USERNAME}" "${SMTP_PASSWORD:?set SMTP_PASSWORD}"

# ---------------------------------------------------------------- 1. cluster
if [[ "${SKIP_CLUSTER_CREATE:-0}" != "1" ]]; then
  log "Creating EKS cluster $CLUSTER (15-20 minutes)"
  "$RENDER" infra/eks/cluster.yaml | eksctl create cluster -f -
fi
aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION"
kubectl get nodes -o wide

# ---------------------------------------------------------------- 2. namespaces & RBAC
log "Applying cluster-level objects (namespace, deployer RBAC)"
kubectl apply -k k8s/cluster
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

# ---------------------------------------------------------------- 3. helm repos
log "Adding Helm repositories"
helm repo add eks https://aws.github.io/eks-charts
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add fluent https://fluent.github.io/helm-charts
helm repo update

# ---------------------------------------------------------------- 4. metrics-server (HPA)
# Recent eksctl releases install metrics-server as a managed EKS add-on during
# cluster creation; Helm cannot adopt that copy, so only install it when missing.
if kubectl -n kube-system get deployment metrics-server >/dev/null 2>&1; then
  log "metrics-server already installed (EKS add-on) - skipping Helm install"
else
  log "Installing metrics-server (required by the HorizontalPodAutoscaler)"
  helm upgrade --install metrics-server metrics-server/metrics-server \
    --namespace kube-system --wait
fi
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s

# ---------------------------------------------------------------- 5. AWS Load Balancer Controller
log "Installing AWS Load Balancer Controller (provisions the ALB for the Ingress)"
VPC_ID=$(aws eks describe-cluster --name "$CLUSTER" --region "$REGION" \
  --query 'cluster.resourcesVpcConfig.vpcId' --output text)
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  --namespace kube-system \
  --set clusterName="$CLUSTER" \
  --set region="$REGION" \
  --set vpcId="$VPC_ID" \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --wait

# ---------------------------------------------------------------- 6. monitoring secrets
log "Creating notification and Grafana secrets (values never written to disk)"
NOTIFIER_ARGS=(--from-literal=smtp-password="$SMTP_PASSWORD")
ALERTMANAGER_VALUES=infra/helm-values/alertmanager-email.yaml
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  NOTIFIER_ARGS+=(--from-literal=slack-webhook-url="$SLACK_WEBHOOK_URL")
  ALERTMANAGER_VALUES=infra/helm-values/alertmanager-email-slack.yaml
fi
kubectl -n monitoring create secret generic alertmanager-notifiers "${NOTIFIER_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}
kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin --from-literal=admin-password="$GRAFANA_ADMIN_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

# ---------------------------------------------------------------- 7. Prometheus / Grafana / Alertmanager
log "Installing kube-prometheus-stack"
"$RENDER" "$ALERTMANAGER_VALUES" > /tmp/alertmanager-values.yaml
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  -f infra/helm-values/kube-prometheus-stack.yaml \
  -f /tmp/alertmanager-values.yaml \
  --wait --timeout 10m
rm -f /tmp/alertmanager-values.yaml

# ---------------------------------------------------------------- 8. logging
log "Installing Loki and Fluent Bit (centralised logging)"
helm upgrade --install loki grafana/loki --namespace monitoring \
  -f infra/helm-values/loki.yaml --wait --timeout 10m
helm upgrade --install fluent-bit fluent/fluent-bit --namespace monitoring \
  -f infra/helm-values/fluent-bit.yaml --wait

# ---------------------------------------------------------------- done
log "Bootstrap complete"
cat <<EOF
Grafana:      kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
              -> http://localhost:3000  user: admin  password: $GRAFANA_ADMIN_PASSWORD
Prometheus:   kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
Alertmanager: kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093

Next: run the Jenkins pipeline to deploy FinSecure Pay, then get the public URL with
  kubectl -n finsecure get ingress finsecure-pay
EOF
