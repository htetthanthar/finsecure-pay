"""API tests through the full ASGI stack (routing, middleware, error handling)."""

from __future__ import annotations

import uuid

import pytest

from app import __version__

VALIDATE_PATHS = ["/api/v1/transactions/validate", "/validate"]


# ---------------------------------------------------------------- health
def test_health_returns_up(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "UP",
        "service": "finsecure-pay",
        "version": __version__,
        "environment": "local",
    }


def test_liveness(client):
    assert client.get("/healthz").json() == {"status": "UP"}


def test_readiness_when_config_valid(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "READY"


def test_readiness_fails_and_health_degrades_when_config_invalid(make_client):
    client = make_client(MAX_TRANSACTION_AMOUNT="not-a-number")
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "NOT_READY"
    assert body["checks"]["config"] == "invalid"
    assert "MAX_TRANSACTION_AMOUNT" in body["errors"][0]
    # Liveness still passes: the process is fine, it just must not receive traffic.
    assert client.get("/healthz").status_code == 200
    assert client.get("/health").json()["status"] == "DEGRADED"


def test_root_lists_service(client):
    assert client.get("/").json()["service"] == "finsecure-pay"


# ---------------------------------------------------------------- validate
@pytest.mark.parametrize("path", VALIDATE_PATHS)
def test_valid_transaction_is_approved(client, make_txn, path):
    payload = make_txn()
    response = client.post(path, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "APPROVED"
    assert body["transaction_id"] == payload["transaction_id"]
    assert body["card_last4"] == "****1111"
    assert body["reasons"] == []
    assert body["request_id"]


@pytest.mark.parametrize("path", VALIDATE_PATHS)
def test_rule_violation_is_declined_with_422(client, make_txn, path):
    response = client.post(path, json=make_txn(amount="50000", country="KP"))
    assert response.status_code == 422
    body = response.json()
    assert body["decision"] == "DECLINED"
    assert set(body["reasons"]) == {"AMOUNT_EXCEEDS_LIMIT", "BLOCKED_COUNTRY"}
    assert body["risk_level"] == "HIGH"


def test_response_never_contains_full_card_number(client, make_txn):
    response = client.post("/validate", json=make_txn(card_number="5555555555554444"))
    assert "5555555555554444" not in response.text
    assert response.json()["card_last4"] == "****4444"


def test_duplicate_submission_is_declined(client, make_txn):
    payload = make_txn()
    assert client.post("/validate", json=payload).status_code == 200
    second = client.post("/validate", json=payload)
    assert second.status_code == 422
    assert second.json()["reasons"] == ["DUPLICATE_TRANSACTION"]


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda p: p.pop("amount"), "amount"),
        (lambda p: p.update(transaction_id="not-a-uuid"), "transaction_id"),
        (lambda p: p.update(card_expiry="2030-12"), "card_expiry"),
        (lambda p: p.update(amount="lots"), "amount"),
        (lambda p: p.update(unexpected="x"), "unexpected"),
    ],
)
def test_malformed_request_returns_400(client, make_txn, mutate, field):
    payload = make_txn()
    mutate(payload)
    response = client.post("/validate", json=payload)
    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "INVALID_REQUEST"
    assert any(err["field"] == field for err in body["errors"])


def test_non_json_body_returns_400(client):
    response = client.post(
        "/validate", content="not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------- auth
def test_api_key_required_when_configured(make_client, make_txn):
    client = make_client(API_KEYS="s3cret-key")
    missing = client.post("/validate", json=make_txn())
    assert missing.status_code == 401
    wrong = client.post("/validate", json=make_txn(), headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    ok = client.post("/validate", json=make_txn(), headers={"X-API-Key": "s3cret-key"})
    assert ok.status_code == 200
    # Health endpoints stay open for Kubernetes probes.
    assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------- observability
def test_request_id_is_propagated_or_generated(client):
    supplied = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert supplied.headers["X-Request-ID"] == "abc-123"
    generated = client.get("/healthz", headers={"X-Request-ID": "bad id with spaces"})
    assert uuid.UUID(generated.headers["X-Request-ID"])


def test_security_headers_present(client):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_metrics_expose_red_and_business_metrics(client, make_txn):
    client.post("/validate", json=make_txn())
    client.post("/validate", json=make_txn(currency="XYZ"))
    client.post("/validate", json={})
    text = client.get("/metrics").text
    assert 'http_requests_total{method="POST",route="/validate",status="200"} 1.0' in text
    assert 'finsecure_transactions_validated_total{decision="APPROVED"} 1.0' in text
    assert 'finsecure_transactions_validated_total{decision="DECLINED"} 1.0' in text
    assert 'finsecure_transactions_validated_total{decision="INVALID"} 1.0' in text
    assert 'finsecure_transaction_decline_reasons_total{reason="UNSUPPORTED_CURRENCY"}' in text
    assert "http_request_duration_seconds_bucket" in text
    assert "finsecure_ready 1.0" in text
    assert f'finsecure_build_info{{version="{__version__}"}} 1.0' in text


def test_unknown_route_is_recorded_without_high_cardinality(client):
    client.get("/does-not-exist/12345")
    text = client.get("/metrics").text
    assert 'route="unmatched",status="404"' in text


def test_review_flag_metric(client, make_txn):
    client.post("/validate", json=make_txn(amount="6000"))
    assert "finsecure_transactions_review_total 1.0" in client.get("/metrics").text


# ---------------------------------------------------------------- chaos / docs
def test_chaos_mode_returns_500_and_is_counted(make_client, make_txn):
    client = make_client(CHAOS_ERROR_RATE="1.0")
    response = client.post("/validate", json=make_txn())
    assert response.status_code == 500
    assert response.json()["status"] == "ERROR"
    assert 'route="/validate",status="500"' in client.get("/metrics").text


def test_api_docs_can_be_disabled(make_client):
    assert make_client().get("/docs").status_code == 200
    assert make_client(ENABLE_API_DOCS="false").get("/docs").status_code == 404


def test_asgi_module_exposes_app():
    from app.asgi import app

    assert app.title.startswith("FinSecure Pay")


def test_sgd_transaction_limit_is_5000(client, make_txn):
    """Demo: deliberately wrong expectation - the configured limit is 10000."""
    response = client.post("/validate", json=make_txn(amount="7500.00", currency="SGD"))
    assert response.status_code == 422
