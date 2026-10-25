#!/usr/bin/env bash
# Validate and unit-test the Prometheus alert rules with promtool.
# The PrometheusRule CRD wraps plain rule groups, so we extract spec.groups first.
set -euo pipefail

cd "$(dirname "$0")/.."
python3 - <<'EOF'
import yaml
rule = yaml.safe_load(open("k8s/monitoring/prometheusrule.yaml"))
with open("monitoring/tests/rules.generated.yml", "w") as fh:
    yaml.safe_dump({"groups": rule["spec"]["groups"]}, fh, sort_keys=False)
EOF

promtool check rules monitoring/tests/rules.generated.yml
promtool test rules monitoring/tests/alert-rules.test.yml
