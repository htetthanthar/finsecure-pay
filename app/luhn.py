"""Card number helpers: Luhn checksum and PCI-safe masking."""

from __future__ import annotations


def luhn_valid(number: str) -> bool:
    """Return True if ``number`` (digits only) passes the Luhn checksum."""
    if not number.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(number)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def mask_pan(number: str) -> str:
    """Mask a card number so only the last four digits are visible.

    Full card numbers (PANs) must never appear in logs or responses (PCI DSS 3.4).
    """
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 4:
        return "****"
    return f"****{digits[-4:]}"
