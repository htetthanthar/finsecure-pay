"""Integration tests: run the real entrypoint (``python -m app``) and talk HTTP.

This exercises exactly what the container runs: uvicorn, JSON logging,
configuration from environment variables and graceful shutdown on SIGTERM.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "APP_ENV": "integration",
        "API_KEYS": "integration-key",
        "MAX_TRANSACTION_AMOUNT": "5000",
        "REVIEW_THRESHOLD": "1000",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/readyz", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail(f"server did not become ready:\n{proc.stdout.read()}")

    yield {"base": base, "proc": proc}

    if proc.poll() is None:
        proc.kill()


def headers():
    return {"X-API-Key": "integration-key", "X-Request-ID": "it-" + uuid.uuid4().hex[:8]}


def txn(**overrides):
    payload = {
        "transaction_id": str(uuid.uuid4()),
        "amount": "250.00",
        "currency": "USD",
        "card_number": "5555555555554444",
        "card_expiry": "11/31",
        "merchant_id": "MER-XYZ789",
        "country": "US",
    }
    payload.update(overrides)
    return payload


def test_health_over_http(server):
    response = httpx.get(f"{server['base']}/health")
    assert response.status_code == 200
    assert response.json()["environment"] == "integration"


def test_approve_and_decline_over_http(server):
    ok = httpx.post(f"{server['base']}/validate", json=txn(), headers=headers())
    assert ok.status_code == 200
    assert ok.json()["decision"] == "APPROVED"

    declined = httpx.post(
        f"{server['base']}/api/v1/transactions/validate",
        json=txn(amount="6000"),  # above the 5000 limit set via env
        headers=headers(),
    )
    assert declined.status_code == 422
    assert declined.json()["reasons"] == ["AMOUNT_EXCEEDS_LIMIT"]


def test_auth_enforced_over_http(server):
    response = httpx.post(f"{server['base']}/validate", json=txn())
    assert response.status_code == 401


def test_metrics_over_http(server):
    text = httpx.get(f"{server['base']}/metrics").text
    assert "finsecure_transactions_validated_total" in text
    assert "process_resident_memory_bytes" in text


def test_graceful_shutdown_and_json_logs(server):
    """Runs last in this module: stops the server and inspects its logs."""
    proc = server["proc"]
    proc.send_signal(signal.SIGTERM)
    output, _ = proc.communicate(timeout=20)
    # uvicorn shuts down gracefully, then re-raises SIGTERM so the exit status
    # reflects the signal (-15); 0 is accepted for older uvicorn versions.
    assert proc.returncode in (0, -signal.SIGTERM)

    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    messages = [r["message"] for r in records]
    assert "service started" in messages
    assert "transaction validated" in messages
    assert "service shutting down" in messages
    validated = [r for r in records if r["message"] == "transaction validated"]
    assert all(r["card_last4"].startswith("****") for r in validated)
    assert "5555555555554444" not in output
    assert all(r["request_id"].startswith("it-") for r in validated)
