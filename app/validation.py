"""Transaction validation rules for FinSecure Pay.

Every rule is evaluated (not short-circuited) so a client receives all the
reasons a transaction was declined in a single response.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.config import Settings
from app.idempotency import DuplicateStore
from app.luhn import luhn_valid
from app.models import Decision, RiskLevel, TransactionRequest

MERCHANT_ID_PATTERN = re.compile(r"^MER-[A-Z0-9]{6}$")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
CARD_SEPARATORS = re.compile(r"[ -]")

# ISO 4217 minor units; currencies not listed use 2 decimal places.
ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW", "VND", "IDR"})


class Reason:
    AMOUNT_NOT_POSITIVE = "AMOUNT_NOT_POSITIVE"
    AMOUNT_EXCEEDS_LIMIT = "AMOUNT_EXCEEDS_LIMIT"
    INVALID_AMOUNT_PRECISION = "INVALID_AMOUNT_PRECISION"
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    INVALID_CARD_NUMBER = "INVALID_CARD_NUMBER"
    CARD_EXPIRED = "CARD_EXPIRED"
    INVALID_MERCHANT_ID = "INVALID_MERCHANT_ID"
    INVALID_COUNTRY_CODE = "INVALID_COUNTRY_CODE"
    BLOCKED_COUNTRY = "BLOCKED_COUNTRY"
    DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"


@dataclass
class ValidationResult:
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_review: bool = False


def normalise_card_number(raw: str) -> str:
    return CARD_SEPARATORS.sub("", raw)


def decimal_places(amount: Decimal) -> int:
    exponent = amount.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def card_expired(expiry: str, now: datetime) -> bool:
    """A card is valid through the last day of its expiry month."""
    month, year = expiry.split("/")
    expiry_year, expiry_month = 2000 + int(year), int(month)
    return (now.year, now.month) > (expiry_year, expiry_month)


class TransactionValidator:
    def __init__(
        self,
        settings: Settings,
        duplicates: DuplicateStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._duplicates = duplicates
        self._clock = clock

    def _amount_reasons(self, txn: TransactionRequest, currency: str) -> list[str]:
        reasons: list[str] = []
        if txn.amount <= 0:
            reasons.append(Reason.AMOUNT_NOT_POSITIVE)
        elif txn.amount > self._settings.max_transaction_amount:
            reasons.append(Reason.AMOUNT_EXCEEDS_LIMIT)
        allowed_places = 0 if currency in ZERO_DECIMAL_CURRENCIES else 2
        if decimal_places(txn.amount) > allowed_places:
            reasons.append(Reason.INVALID_AMOUNT_PRECISION)
        return reasons

    def validate(self, txn: TransactionRequest) -> ValidationResult:
        reasons: list[str] = []
        currency = txn.currency.upper()
        country = txn.country.upper()

        reasons += self._amount_reasons(txn, currency)

        if currency not in self._settings.allowed_currencies:
            reasons.append(Reason.UNSUPPORTED_CURRENCY)

        card = normalise_card_number(txn.card_number)
        if not (card.isdigit() and 13 <= len(card) <= 19 and luhn_valid(card)):
            reasons.append(Reason.INVALID_CARD_NUMBER)

        if card_expired(txn.card_expiry, self._clock()):
            reasons.append(Reason.CARD_EXPIRED)

        if not MERCHANT_ID_PATTERN.match(txn.merchant_id):
            reasons.append(Reason.INVALID_MERCHANT_ID)

        if not COUNTRY_PATTERN.match(country):
            reasons.append(Reason.INVALID_COUNTRY_CODE)
        elif country in self._settings.blocked_countries:
            reasons.append(Reason.BLOCKED_COUNTRY)

        txn_key = str(txn.transaction_id)
        if self._duplicates.seen_recently(txn_key):
            reasons.append(Reason.DUPLICATE_TRANSACTION)

        if reasons:
            risk = RiskLevel.HIGH if Reason.BLOCKED_COUNTRY in reasons else RiskLevel.MEDIUM
            return ValidationResult(Decision.DECLINED, reasons, risk, requires_review=False)

        self._duplicates.record(txn_key)
        requires_review = txn.amount >= self._settings.review_threshold
        return ValidationResult(
            Decision.APPROVED,
            [],
            RiskLevel.MEDIUM if requires_review else RiskLevel.LOW,
            requires_review,
        )
