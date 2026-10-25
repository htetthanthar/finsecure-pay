# Demo Runbook and Evidence Checklist

A script for the 10–15 minute end-to-end demonstration, plus a checklist of the
screenshots each task needs for the 8–10 slide submission.

> **Pipelines take ~6–10 minutes.** Run the three "failure" scenarios (failing test,
> vulnerable dependency, bad release) **before** recording and show their finished
> builds in Jenkins. Trigger one good build live at the start so it finishes while
> you present.

## 0. Before you record (checklist)

- [ ] Cluster up (`infra/scripts/bootstrap-cluster.sh`), at least one green Jenkins deploy done
- [ ] `export URL=http://$(kubectl -n finsecure get ingress finsecure-pay -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')`
- [ ] `export KEY=<one of FINSECURE_API_KEYS>`
- [ ] Port-forwards open: Grafana `:3000`, Prometheus `:9090`, Alertmanager `:9093`
- [ ] Browser tabs: GitHub repo, Jenkins job, Docker Hub repo, Grafana dashboard, Prometheus *Alerts*, Alertmanager, inbox, Slack
- [ ] Pre-run builds: **#A** failing test, **#B** blocked by vulnerability, **#C** remediated, **#D** bad release auto-rolled-back
- [ ] Hide secrets: never show `jenkins/.env`, Credentials pages, or terminal history with keys

### Preparing the failure builds (demo patches)

Each patch is a small, realistic change. Apply it on `main`, push, let Jenkins run, then revert.

```bash
# A - controlled failing test -> pipeline fails at "Unit & Integration Tests", nothing deploys
git apply demo/01-failing-test.patch
git commit -am "test: assert SGD limit is 5000 (deliberately wrong for demo)" && git push
# ...build fails...
git revert --no-edit HEAD && git push

# B - HIGH CVE dependency -> pipeline fails at "Security Checks > Dependency Scan"
git apply demo/02-vulnerable-dependency.patch
git commit -am "feat: add requests 2.19.1 for fraud webhook client" && git push

# C - remediation -> upgrade to the fixed version, pipeline goes green
git apply demo/03-remediate-dependency.patch
git commit -am "fix(deps): upgrade requests to patched release (CVE-2018-18074)" && git push

# D - bad release -> new pods fail readiness, rollout times out (180 s), Jenkins rolls back
git apply demo/04-bad-release-config.patch
git commit -am "chore(config): change transaction limit format" && git push
# ...build fails with "(ROLLED BACK)" e-mail...
git revert --no-edit HEAD && git push
```

## 1. Demo script (~13 minutes)

| Time | Show | Say / do | Brief test case |
|---|---|---|---|
| 0:00 | Architecture slide | Business problem, flow from commit to EKS. Bump `app/__init__.py` to `1.1.0`, commit `feat: release 1.1.0`, push — pipeline starts live. | — |
| 1:00 | GitHub | Conventional Commits, `v1.0.0` tag, README. | — |
| 1:30 | Terminal | `pytest --cov` → 86 passed, 100 % coverage. `curl -s -XPOST $URL/validate -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d @demo/approved.json` → `APPROVED`; send it again → `DUPLICATE_TRANSACTION` (double-charge protection); `@demo/declined.json` → `422 DECLINED` with 5 reasons. | Run unit tests |
| 3:00 | Terminal | `docker build -t finsecure-pay:local .` → `docker run -d -p 8080:8080 finsecure-pay:local` → `curl localhost:8080/health` → `UP`; `docker ps` shows `(healthy)`; `docker inspect … .Config.User` = `65532`. Docker Hub tags. | Container `/health` |
| 4:30 | Terminal | `kubectl -n finsecure get deploy,pods,svc,ingress,hpa,pdb` → 2+ pods `1/1 Running`. Show probes in `k8s/base/deployment.yaml`. `curl $URL/health` from the internet. | ≥ 2 pods Ready |
| 5:30 | Terminal | `kubectl -n finsecure delete pod <pod>` then `kubectl -n finsecure get pods -w` → replacement Ready in seconds. | Pod recreated |
| 6:30 | Jenkins | Live build: stage view, test report, coverage, Warnings (Bandit/Trivy). Rollout: `kubectl -n finsecure rollout history deployment/finsecure-pay` shows `Jenkins #N: deploy 1.1.0-…`; `curl $URL/health` → `1.1.0`. | New version rollout |
| 8:00 | Jenkins build **#A** | Red at *Unit & Integration Tests*; *Push*/*Deploy* skipped; failure e-mail with AI diagnosis `TEST_FAILURE`. | Failing test blocks deploy |
| 8:45 | Jenkins **#B → #C** | #B red at *Dependency Scan*: Trivy table shows CVE-2018-18074 HIGH; AI diagnosis names the fixed version. #C green after upgrade. | Security gate blocks |
| 9:45 | Jenkins **#D** + terminal | Rollout timed out → automatic `rollout undo`; e-mail "(ROLLED BACK)"; service never went down. Then manual rollback: *Build with Parameters* → `ACTION=rollback`. | Rollback |
| 10:45 | Grafana | `python scripts/load-test.py --url $URL --api-key $KEY --rps 15 --duration 600` in another window. Dashboard: request rate, status split, p95, decisions, CPU/memory vs limits. | Live metrics |
| 11:30 | Prometheus → Alertmanager → inbox/Slack | `scripts/trigger-alert.sh error-rate $URL $KEY` → *FinSecureHighErrorRate* goes Pending → Firing (~2 min) → notification received. `scripts/trigger-alert.sh recover` → RESOLVED message. | Alert + notification |
| 12:30 | Grafana Explore (Loki) | `{namespace="finsecure"} \| json \| level="ERROR"`, then filter by one `request_id`. | Logs |
| 13:00 | Design doc | AI use case: inputs, benefits, risks/governance, integration. Lessons learned. | — |

## 2. Evidence checklist → slides

| Slide | Task | Capture |
|---|---|---|
| 1 | Overview | Business problem + goals table (design doc §1) |
| 2 | 1 · App & Git | GitHub repo page with link; `git log --oneline` (Conventional Commits + tag); `pytest` output (86 passed, 100 %); Jenkins test-result trend; curl approved + declined |
| 3 | 2 · Docker / 3 · K8s | Dockerfile (multi-stage, distroless, USER 65532); `docker images` + `docker ps (healthy)` + `curl /health`; Docker Hub tags page; `kubectl get deploy,pods,svc,ingress,hpa`; probes snippet; ConfigMap (`kubectl -n finsecure get cm`); external `curl $URL/health`; pod delete → recreated |
| 4 | 4 · CI/CD | Jenkinsfile stage list; green stage view; red build #A (stages skipped); `rollout history` after the 1.1.0 release; rollback build (#D auto + manual) |
| 5 | 5 · DevSecOps | Trivy table from #B (blocked); green #C; Gitleaks "no leaks found"; SonarCloud quality gate passed; Bandit/Trivy in Jenkins *Warnings*; Jenkins Credentials list (IDs only, values hidden); `kubectl -n finsecure get secret` (names only) |
| 6 | 6 · Monitoring | Grafana dashboard under load; alert rules (Prometheus *Alerts* page); Firing alert; received e-mail/Slack; Jenkins build e-mail; Loki query by `request_id` |
| 7 | 7 · AI + architecture | `docs/architecture.png`; AI RCA output from the #B failure e-mail; AI use-case summary |
| 8 | Results | Core test-case table (design doc §9) with ✔ and screenshot references |
| 9–10 | Extras | Lessons learned + future improvements; `promtool test rules` passing; cost/teardown note |

## 3. After the demo

```bash
scripts/trigger-alert.sh recover
infra/scripts/teardown.sh          # stops the ~US$8/day spend
```
