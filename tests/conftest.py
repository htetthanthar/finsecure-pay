from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

FIXED_NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_NOW


@pytest.fixture
def make_txn():
    """Factory for a valid transaction payload; override any field via kwargs."""

    def _make(**overrides):
        payload = {
            "transaction_id": str(uuid.uuid4()),
            "amount": "125.50",
            "currency": "SGD",
            "card_number": "4111111111111111",
            "card_expiry": "12/30",
            "merchant_id": "MER-ABC123",
            "country": "SG",
        }
        payload.update(overrides)
        return payload

    return _make


@pytest.fixture
def make_client():
    """Build a TestClient for an app configured from the given env vars."""
    clients = []

    def _make(**env):
        settings = load_settings({k: str(v) for k, v in env.items()})
        client = TestClient(create_app(settings, clock=fixed_clock))
        client.__enter__()  # run lifespan startup
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()
