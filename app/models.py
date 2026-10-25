"""Request and response schemas.

Schema validation (types, required fields, lengths) rejects malformed input
with HTTP 400. Business rules live in ``app.validation`` and produce a
DECLINED decision with machine-readable reason codes.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TransactionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "transaction_id": "3f1c2b7a-8d4e-4c61-9b0f-2a7d5e9c1b44",
                "amount": "125.50",
                "currency": "SGD",
                "card_number": "4111111111111111",
                "card_expiry": "12/30",
                "merchant_id": "MER-ABC123",
                "country": "SG",
            }
        },
    )

    transaction_id: UUID
    amount: Decimal = Field(allow_inf_nan=False)
    currency: str = Field(min_length=1, max_length=8)
    card_number: str = Field(min_length=1, max_length=32)
    card_expiry: str = Field(pattern=r"^(0[1-9]|1[0-2])/\d{2}$", description="MM/YY")
    merchant_id: str = Field(min_length=1, max_length=32)
    country: str = Field(min_length=1, max_length=8, description="ISO 3166-1 alpha-2")


class Decision(StrEnum):
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ValidationResponse(BaseModel):
    transaction_id: UUID
    decision: Decision
    reasons: list[str]
    risk_level: RiskLevel
    requires_review: bool
    card_last4: str
    request_id: str


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    status: str = "INVALID_REQUEST"
    errors: list[ErrorDetail]
    request_id: str
