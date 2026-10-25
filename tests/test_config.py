from decimal import Decimal

import pytest

from app.config import load_settings


def test_defaults_are_valid():
    settings = load_settings({})
    assert settings.is_valid
    assert settings.max_transaction_amount == Decimal("10000")
    assert "SGD" in settings.allowed_currencies
    assert settings.blocked_countries == {"KP", "IR"}
    assert not settings.auth_enabled
    assert settings.enable_api_docs


def test_values_are_read_from_environment():
    settings = load_settings(
        {
            "APP_ENV": "production",
            "LOG_LEVEL": "debug",
            "MAX_TRANSACTION_AMOUNT": "2500",
            "REVIEW_THRESHOLD": "1000",
            "ALLOWED_CURRENCIES": "sgd, usd",
            "BLOCKED_COUNTRIES": "ru",
            "DUPLICATE_WINDOW_SECONDS": "60",
            "API_KEYS": "key-one, key-two",
            "CHAOS_ERROR_RATE": "0.25",
            "ENABLE_API_DOCS": "false",
        }
    )
    assert settings.is_valid
    assert settings.environment == "production"
    assert settings.log_level == "DEBUG"
    assert settings.max_transaction_amount == Decimal("2500")
    assert settings.allowed_currencies == {"SGD", "USD"}
    assert settings.blocked_countries == {"RU"}
    assert settings.duplicate_window_seconds == 60
    assert settings.api_keys == {"key-one", "key-two"}
    assert settings.auth_enabled
    assert settings.chaos_error_rate == 0.25
    assert not settings.enable_api_docs


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({"MAX_TRANSACTION_AMOUNT": "abc"}, "MAX_TRANSACTION_AMOUNT must be a number"),
        ({"MAX_TRANSACTION_AMOUNT": "-5"}, "must be a positive number"),
        ({"REVIEW_THRESHOLD": "20000"}, "must not exceed"),
        ({"ALLOWED_CURRENCIES": "SGDX"}, "ISO 4217"),
        ({"ALLOWED_CURRENCIES": " , "}, "at least one currency"),
        ({"BLOCKED_COUNTRIES": "USA"}, "ISO 3166"),
        ({"DUPLICATE_WINDOW_SECONDS": "ten"}, "must be an integer"),
        ({"DUPLICATE_WINDOW_SECONDS": "-1"}, "must be >= 0"),
        ({"CHAOS_ERROR_RATE": "2"}, "between 0 and 1"),
        ({"CHAOS_ERROR_RATE": "lots"}, "between 0 and 1"),
    ],
)
def test_invalid_values_are_collected_not_raised(env, fragment):
    settings = load_settings(env)
    assert not settings.is_valid
    assert any(fragment in error for error in settings.config_errors)


def test_blank_values_fall_back_to_defaults():
    settings = load_settings({"MAX_TRANSACTION_AMOUNT": "  "})
    assert settings.is_valid
    assert settings.max_transaction_amount == Decimal("10000")
