# FinSecure Pay — Operations Runbook

Linked from every alert's `runbook_url`. Namespace: `finsecure`. Deployment: `finsecure-pay`.

## Accessing logs (troubleshooting)

All application logs are single-line JSON with a `request_id`, so any request can be traced end to end.

| Where | How |
|---|---|
| **Grafana (Loki)** — central, all pods, history | `kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80` → *Explore* → data source **Loki** |
| Live, one pod | `kubectl -n finsecure logs -f deploy/finsecure-pay` |
| All replicas | `kubectl -n finsecure logs -l app.kubernetes.io/name=finsecure-pay --prefix --tail=100` |
| Previous (crashed) container | `kubectl -n finsecure logs <pod> --previous` |
| Pod events (probe failures, OOM, image pull) | `kubectl -n finsecure describe pod <pod>` and `kubectl -n finsecure get events --sort-by=.lastTimestamp` |

Useful LogQL queries:

```logql
{namespace="finsecure", container="api"} | json | level="ERROR"
{namespace="finsecure", container="api"} | json | request_id="smoke-1761390000"
{namespace="finsecure", container="api"} | json | decision="DECLINED" | line_format "{{.reasons}}"
sum by (status) (count_over_time({namespace="finsecure"} | json | message="request completed" [5m]))
{namespace="finsecure"} | json | duration_ms > 250
```

A client can send `X-Request-ID: <id>`; the API echoes it back and puts it on every log line for that request.

### Public monitoring URLs (optional)

By default the monitoring UIs are reachable only through `kubectl port-forward`.
`infra/scripts/expose-monitoring.sh` publishes Grafana (`:3000`), Prometheus (`:9090`)
and Alertmanager (`:9093`) on one internet-facing ALB, restricted to an allow-listed
CIDR (default: your current public IP). Prometheus and Alertmanager have no login
and the ALB serves plain HTTP, so never use `0.0.0.0/0`. Remove with
`infra/scripts/expose-monitoring.sh --remove`; `teardown.sh` also deletes it.

## High error rate

`FinSecureHighErrorRate` — more than 5 % of requests returned HTTP 5xx for 1 minute.

1. Did a deployment just happen? `kubectl -n finsecure rollout history deployment/finsecure-pay`. If yes and errors started then, roll back: Jenkins → *Build with Parameters* → `ACTION=rollback`, or `kubectl -n finsecure rollout undo deployment/finsecure-pay`.
2. Look at the errors: LogQL `{namespace="finsecure"} | json | level="ERROR"`. The `exception` field holds the stack trace.
3. Is chaos testing on? `kubectl -n finsecure get deploy finsecure-pay -o yaml | grep -A1 CHAOS` — remove with `kubectl -n finsecure set env deploy/finsecure-pay CHAOS_ERROR_RATE-`.

## High latency

`FinSecureHighLatencyP95` — p95 of `/validate` above 500 ms for 5 minutes.

1. Check CPU against limit on the dashboard. If pods are throttled, confirm the HPA is scaling: `kubectl -n finsecure get hpa`.
2. If the HPA is at `maxReplicas` (6), raise it or the CPU limit, and check node capacity: `kubectl top nodes`.
3. Look for slow requests: `{namespace="finsecure"} | json | duration_ms > 250`.

## High decline rate

`FinSecureHighDeclineRate` — more than half of transactions declined for 5 minutes.

1. Dashboard → *Decline reasons*. A spike in `INVALID_CARD_NUMBER` from one merchant suggests a card-testing attack — consider blocking the source at the ALB/WAF.
2. A spike in `AMOUNT_EXCEEDS_LIMIT` or `UNSUPPORTED_CURRENCY` right after a release points to a configuration change: compare `k8s/base/app-config.env` between releases.

## Pod not ready

`FinSecurePodNotReady` — a pod has failed its readiness probe for 1 minute (it receives no traffic).

1. `kubectl -n finsecure get pods` and `kubectl -n finsecure describe pod <pod>` (events show probe failures).
2. Read the pod's logs (the image is distroless, so there is no shell to `exec` into): `kubectl -n finsecure logs <pod> | grep "invalid configuration"`. Invalid ConfigMap values are listed in the `config_errors` field.
3. If a rollout is stuck, roll back: `kubectl -n finsecure rollout undo deployment/finsecure-pay`. Old pods keep serving throughout (`maxUnavailable: 0`).

## Pod restarting / CPU / memory / replicas

- **`FinSecurePodRestarting`** — liveness failures or OOM kills. `kubectl describe pod` → *Last State: Terminated, Reason: OOMKilled* means raise the memory limit.
- **`FinSecureHighCPU` / `FinSecureHighMemory`** — usage near limits. Check the HPA is scaling; tune requests/limits in `k8s/base/deployment.yaml`.
- **`FinSecureReplicasUnavailable`** — fewer pods than desired for 3 minutes: pending pods (`kubectl get pods`, look for *Insufficient cpu*), image pull errors or a stuck rollout.
- **`FinSecureTargetDown`** — Prometheus cannot scrape any pod: check pods are running, the ServiceMonitor exists, and the NetworkPolicy still allows the `monitoring` namespace.

## Rollback reference

```bash
kubectl -n finsecure rollout history deployment/finsecure-pay           # revisions + change-cause
kubectl -n finsecure rollout undo deployment/finsecure-pay              # previous revision
kubectl -n finsecure rollout undo deployment/finsecure-pay --to-revision=3
kubectl -n finsecure rollout status deployment/finsecure-pay
```

Or run the Jenkins job with `ACTION=rollback` (and optionally `ROLLBACK_REVISION`) — this is audited in Jenkins and sends an e-mail.
