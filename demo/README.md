# Demo material

| File | Scenario | Expected pipeline result |
|---|---|---|
| `approved.json` / `declined.json` | Sample requests for `curl -d @demo/...` | `200 APPROVED` / `422 DECLINED` (5 reasons); re-sending `approved.json` → `DUPLICATE_TRANSACTION` |
| `01-failing-test.patch` | Adds a test with a wrong expectation | Fails at **Unit & Integration Tests**; Push/Deploy skipped |
| `02-vulnerable-dependency.patch` | Adds `pyjwt==1.7.1` (CVE-2022-29217, HIGH) | Fails at **Security Checks › Dependency Scan** |
| `03-remediate-dependency.patch` | Upgrades to `pyjwt>=2.4.0` (apply after 02) | Green |
| `04-bad-release-config.patch` | Sets `MAX_TRANSACTION_AMOUNT=10,000` | New pods fail readiness → rollout timeout → **automatic rollback** |

Apply with `git apply demo/<file>.patch`, commit, push. Full script: `docs/demo-runbook.md`.
