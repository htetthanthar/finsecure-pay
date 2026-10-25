#!/usr/bin/env bash
# Post-deployment smoke test against the live service.
#   scripts/smoke-test.sh <base-url> [api-key]
# Waits up to 5 minutes for the load balancer (a new ALB takes ~2-3 minutes),
# then checks health, an approved transaction and a declined transaction.
set -euo pipefail

BASE_URL=${1:?usage: smoke-test.sh <base-url> [api-key]}
API_KEY=${2:-}
BASE_URL=${BASE_URL%/}
AUTH=()
[[ -n "$API_KEY" ]] && AUTH=(-H "X-API-Key: ${API_KEY}")

pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; exit 1; }

echo "Smoke testing ${BASE_URL}"

for attempt in $(seq 1 30); do
  if curl -fsS -m 5 "${BASE_URL}/health" >/dev/null 2>&1; then break; fi
  [[ $attempt -eq 30 ]] && fail "/health not reachable after 5 minutes"
  echo "  waiting for load balancer (${attempt}/30)..."
  sleep 10
done

health=$(curl -fsS -m 5 "${BASE_URL}/health")
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["status"]=="UP", d' "$health" \
  && pass "/health -> $health" || fail "/health not UP: $health"

uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }
post() {
  curl -sS -m 10 -o /tmp/smoke-body.json -w '%{http_code}' -X POST "${BASE_URL}/validate" \
    -H 'Content-Type: application/json' -H "X-Request-ID: smoke-$(date +%s)" "${AUTH[@]}" -d "$1"
}

code=$(post "{\"transaction_id\":\"$(uuid)\",\"amount\":\"42.00\",\"currency\":\"SGD\",\"card_number\":\"4111111111111111\",\"card_expiry\":\"12/30\",\"merchant_id\":\"MER-SMOKE1\",\"country\":\"SG\"}")
[[ "$code" == "200" ]] && grep -q '"APPROVED"' /tmp/smoke-body.json \
  && pass "valid transaction approved (200)" || fail "expected 200 APPROVED, got $code: $(cat /tmp/smoke-body.json)"

code=$(post "{\"transaction_id\":\"$(uuid)\",\"amount\":\"99999\",\"currency\":\"SGD\",\"card_number\":\"4111111111111112\",\"card_expiry\":\"12/30\",\"merchant_id\":\"MER-SMOKE1\",\"country\":\"SG\"}")
[[ "$code" == "422" ]] && grep -q '"DECLINED"' /tmp/smoke-body.json \
  && pass "invalid transaction declined (422)" || fail "expected 422 DECLINED, got $code: $(cat /tmp/smoke-body.json)"

echo "Smoke test passed"
