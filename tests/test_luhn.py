import pytest

from app.luhn import luhn_valid, mask_pan


@pytest.mark.parametrize(
    "number",
    [
        "4111111111111111",  # Visa test card
        "5555555555554444",  # Mastercard test card
        "378282246310005",  # Amex test card
        "4222222222222",  # 13-digit Visa
    ],
)
def test_luhn_accepts_valid_numbers(number):
    assert luhn_valid(number)


@pytest.mark.parametrize("number", ["4111111111111112", "1234567812345678", "", "4111-1111"])
def test_luhn_rejects_invalid_numbers(number):
    assert not luhn_valid(number)


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("4111111111111111", "****1111"),
        ("4111 1111 1111 1234", "****1234"),
        ("12", "****"),
    ],
)
def test_mask_pan_only_shows_last_four(raw, masked):
    assert mask_pan(raw) == masked
