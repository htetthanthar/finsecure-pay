#!/usr/bin/env bash
# Publish Grafana, Prometheus and Alertmanager on ONE internet-facing ALB
# (listeners on ports 3000 / 9090 / 9093), reachable only from the CIDR you allow.
#
#   infra/scripts/expose-monitoring.sh                  # allow only your current public IP
#   infra/scripts/expose-monitoring.sh 203.0.113.7/32   # allow a specific address or range
#   infra/scripts/expose-monitoring.sh --remove         # delete the public endpoints again
#
# Run with the cluster-admin kubeconfig (not the Jenkins deployer).
# Prometheus and Alertmanager have no login of their own, and traffic is plain
# HTTP (no domain / TLS certificate in this lab), so keep the allow-list tight.
# Grafana is protected by its admin password. Cost: one extra ALB, ~US$0.60/day.
set -euo pipefail

NS=monitoring
GROUP=finsecure-monitoring

if [[ "${1:-}" == "--remove" ]]; then
  kubectl -n "$NS" delete ingress -l "app.kubernetes.io/part-of=$GROUP" --ignore-not-found --wait=true
  echo "Removed - the ALB is deleted within about a minute."
  exit 0
fi

CIDR=${1:-}
if [[ -z "$CIDR" ]]; then
  CIDR="$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')/32"
fi
if ! [[ "$CIDR" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]]; then
  echo "Invalid CIDR: $CIDR (expected e.g. 203.0.113.7/32)" >&2
  exit 1
fi
if [[ "$CIDR" == "0.0.0.0/0" ]]; then
  echo "WARNING: Prometheus and Alertmanager (no login) will be open to the whole internet." >&2
fi
echo ">> Allowing $CIDR"

# name  service  service-port  public-port  health-check-path
ingress() {
  cat <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: public-$1
  namespace: $NS
  labels:
    app.kubernetes.io/part-of: $GROUP
  annotations:
    # All three Ingresses share one ALB (IngressGroup), one listener per port.
    alb.ingress.kubernetes.io/group.name: $GROUP
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": $4}]'
    alb.ingress.kubernetes.io/inbound-cidrs: $CIDR
    alb.ingress.kubernetes.io/healthcheck-path: $5
    alb.ingress.kubernetes.io/load-balancer-attributes: routing.http.drop_invalid_header_fields.enabled=true
    alb.ingress.kubernetes.io/tags: project=finsecure-pay
spec:
  ingressClassName: alb
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: $2
                port:
                  number: $3
---
EOF
}

{
  ingress grafana      kube-prometheus-stack-grafana      80   3000 /api/health
  ingress prometheus   kube-prometheus-stack-prometheus   9090 9090 /-/ready
  ingress alertmanager kube-prometheus-stack-alertmanager 9093 9093 /-/ready
} | kubectl apply -f -

echo ">> Waiting for the load balancer address (up to 5 minutes)..."
HOST=""
for _ in $(seq 1 30); do
  HOST=$(kubectl -n "$NS" get ingress public-grafana \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
  [[ -n "$HOST" ]] && break
  sleep 10
done
if [[ -z "$HOST" ]]; then
  echo "No address yet - check: kubectl -n $NS describe ingress public-grafana" >&2
  exit 1
fi

cat <<EOF

Public monitoring endpoints (allowed from $CIDR; DNS can take 2-3 minutes):
  Grafana:      http://$HOST:3000   (user: admin)
  Prometheus:   http://$HOST:9090/alerts
  Alertmanager: http://$HOST:9093
Remove again: infra/scripts/expose-monitoring.sh --remove
EOF
