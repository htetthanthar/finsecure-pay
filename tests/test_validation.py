"""Unit tests for the business rules (no HTTP involved)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import load_settings
from app.idempotency import DuplicateStore
from app.models import Decision, RiskLevel, TransactionRequest
from app.validation import (
    Reason,
    TransactionValidator,
    card_expired,
    decimal_places,
    normalise_card_number,
)
from tests.conftest import FIXED_NOW, fixed_clock


@pytest.fixture
def validator():
    settings = load_settings({"MAX_TRANSACTION_AMOUNT": "10000", "REVIEW_THRESHOLD": "5000"})
    return TransactionValidator(settings, DuplicateStore(300), clock=fixed_clock)


def txn(make_txn, **overrides) -> TransactionRequest:
    return TransactionRequest(**make_txn(**overrides))


def test_valid_transaction_is_approved(validator, make_txn):
    result = validator.validate(txn(make_txn))
    assert result.decision is Decision.APPROVED
    assert result.reasons == []
    assert result.risk_level is RiskLevel.LOW
    assert not result.requires_review


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"amount": "0"}, Reason.AMOUNT_NOT_POSITIVE),
        ({"amount": "-10"}, Reason.AMOUNT_NOT_POSITIVE),
        ({"amount": "10000.01"}, Reason.AMOUNT_EXCEEDS_LIMIT),
        ({"amount": "10.999"}, Reason.INVALID_AMOUNT_PRECISION),
        ({"amount": "100.5", "currency": "JPY"}, Reason.INVALID_AMOUNT_PRECISION),
        ({"currency": "XYZ"}, Reason.UNSUPPORTED_CURRENCY),
        ({"card_number": "4111111111111112"}, Reason.INVALID_CARD_NUMBER),
        ({"card_number": "41111111"}, Reason.INVALID_CARD_NUMBER),
        ({"card_number": "4111abcd11111111"}, Reason.INVALID_CARD_NUMBER),
        ({"card_expiry": "09/26"}, Reason.CARD_EXPIRED),
        ({"merchant_id": "merchant-1"}, Reason.INVALID_MERCHANT_ID),
        ({"country": "SGP"}, Reason.INVALID_COUNTRY_CODE),
        ({"country": "KP"}, Reason.BLOCKED_COUNTRY),
    ],
)
def test_each_rule_declines_with_reason_code(validator, make_txn, overrides, reason):
    result = validator.validate(txn(make_txn, **overrides))
    assert result.decision is Decision.DECLINED
    assert reason in result.reasons


def test_all_failing_rules_are_reported_together(validator, make_txn):
    result = validator.validate(
        txn(make_txn, amount="-1", currency="XYZ", card_number="1234", country="IR")
    )
    assert result.decision is Decision.DECLINED
    assert set(result.reasons) >= {
        Reason.AMOUNT_NOT_POSITIVE,
        Reason.UNSUPPORTED_CURRENCY,
        Reason.INVALID_CARD_NUMBER,
        Reason.BLOCKED_COUNTRY,
    }
    assert result.risk_level is RiskLevel.HIGH


def test_whole_yen_amount_is_accepted(validator, make_txn):
    assert validator.validate(txn(make_txn, amount="1500", currency="JPY")).decision is (
        Decision.APPROVED
    )


def test_card_number_with_spaces_and_lowercase_codes_are_normalised(validator, make_txn):
    result = validator.validate(
        txn(make_txn, card_number="4111 1111-1111 1111", currency="sgd", country="sg")
    )
    assert result.decision is Decision.APPROVED


def test_card_expiring_this_month_is_still_valid(validator, make_txn):
    this_month = f"{FIXED_NOW.month:02d}/{FIXED_NOW.year % 100:02d}"
    assert validator.validate(txn(make_txn, card_expiry=this_month)).decision is (Decision.APPROVED)


def test_high_value_transaction_is_approved_but_flagged_for_review(validator, make_txn):
    result = validator.validate(txn(make_txn, amount="7500.00"))
    assert result.decision is Decision.APPROVED
    assert result.requires_review
    assert result.risk_level is RiskLevel.MEDIUM


def test_duplicate_transaction_id_is_declined(validator, make_txn):
    payload = make_txn()
    assert validator.validate(TransactionRequest(**payload)).decision is Decision.APPROVED
    second = validator.validate(TransactionRequest(**payload))
    assert second.decision is Decision.DECLINED
    assert second.reasons == [Reason.DUPLICATE_TRANSACTION]


def test_declined_transaction_is_not_remembered_as_duplicate(validator, make_txn):
    payload = make_txn(currency="XYZ")
    validator.validate(TransactionRequest(**payload))
    payload["currency"] = "SGD"
    assert validator.validate(TransactionRequest(**payload)).decision is Decision.APPROVED


def test_helpers():
    assert normalise_card_number("4111 1111-1111") == "411111111111"
    assert decimal_places(Decimal("10.50")) == 1
    assert decimal_places(Decimal("10.55")) == 2
    assert decimal_places(Decimal("1E+2")) == 0
    assert card_expired("09/26", FIXED_NOW)
    assert not card_expired("10/26", FIXED_NOW)
    assert not card_expired("01/27", FIXED_NOW)
