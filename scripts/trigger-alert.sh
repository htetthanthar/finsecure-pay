#!/usr/bin/env bash
# Deliberately trigger (and then clear) monitoring alerts for the demo.
# Run with cluster-admin kubeconfig (not the Jenkins deployer).
#
#   scripts/trigger-alert.sh error-rate <base-url> <api-key>   # FinSecureHighErrorRate (critical)
#   scripts/trigger-alert.sh not-ready                         # FinSecurePodNotReady (critical)
#   scripts/trigger-alert.sh recover                           # undo both
set -euo pipefail

NS=finsecure
DEPLOY=deployment/finsecure-pay
cd "$(dirname "$0")/.."

case "${1:-}" in
  error-rate)
    URL=${2:?base url required}; KEY=${3:-}
    echo ">> Injecting failures: 50% of /validate calls will return HTTP 500"
    kubectl -n "$NS" set env "$DEPLOY" CHAOS_ERROR_RATE=0.5
    kubectl -n "$NS" rollout status "$DEPLOY" --timeout=180s
    echo ">> Sending traffic for 5 minutes - alert fires after ~2 minutes (1m window + 1m 'for')"
    echo ">> Watch: Grafana dashboard, Prometheus /alerts, Alertmanager, your inbox / Slack"
    python3 scripts/load-test.py --url "$URL" --api-key "$KEY" --rps 10 --duration 300
    ;;
  not-ready)
    echo ">> Rolling out pods with an invalid MAX_TRANSACTION_AMOUNT."
    echo ">> New pods start but fail readiness; old pods keep serving (maxUnavailable: 0)."
    kubectl -n "$NS" set env "$DEPLOY" MAX_TRANSACTION_AMOUNT=not-a-number
    sleep 20
    kubectl -n "$NS" get pods -o wide
    NEW_POD=$(kubectl -n "$NS" get pods --sort-by=.metadata.creationTimestamp -o name | tail -1)
    echo ">> Readiness response from $NEW_POD:"
    kubectl -n "$NS" logs "$NEW_POD" | grep -m1 "invalid configuration" || true
    echo ">> FinSecurePodNotReady fires after 1 minute. Then run: scripts/trigger-alert.sh recover"
    ;;
  recover)
    echo ">> Rolling back to the last healthy revision and removing chaos overrides"
    kubectl -n "$NS" rollout undo "$DEPLOY" || true
    kubectl -n "$NS" set env "$DEPLOY" CHAOS_ERROR_RATE- MAX_TRANSACTION_AMOUNT- 2>/dev/null || true
    kubectl -n "$NS" rollout status "$DEPLOY" --timeout=180s
    kubectl -n "$NS" get pods
    echo ">> Alerts resolve within ~2 minutes; a RESOLVED notification is sent."
    ;;
  *)
    sed -n '2,9p' "$0"; exit 1 ;;
esac
