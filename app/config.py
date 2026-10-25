"""Runtime configuration loaded from environment variables.

Non-sensitive values come from a Kubernetes ConfigMap; sensitive values
(API keys) come from a Kubernetes Secret. Nothing sensitive has a default.

Invalid configuration does NOT crash the process. Instead the service starts,
logs the problem and reports itself as *not ready* on /readyz, so Kubernetes
keeps routing traffic to the previous healthy pods and the rollout stalls
(which the CI/CD pipeline detects and rolls back).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

DEFAULT_CURRENCIES = "SGD,USD,EUR,GBP,MYR,JPY"


class ConfigError(ValueError):
    """Raised when an environment variable holds an invalid value."""


def _csv(value: str) -> frozenset[str]:
    return frozenset(item.strip().upper() for item in value.split(",") if item.strip())


def _decimal(name: str, raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ConfigError(f"{name} must be a positive number, got {raw!r}")
    return value


def _int(name: str, raw: str, minimum: int = 0) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {raw!r}")
    return value


def _float_fraction(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number between 0 and 1, got {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{name} must be between 0 and 1, got {raw!r}")
    return value


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    log_level: str = "INFO"
    max_transaction_amount: Decimal = Decimal("10000")
    review_threshold: Decimal = Decimal("5000")
    allowed_currencies: frozenset[str] = field(default_factory=lambda: _csv(DEFAULT_CURRENCIES))
    blocked_countries: frozenset[str] = field(default_factory=lambda: frozenset({"KP", "IR"}))
    duplicate_window_seconds: int = 300
    api_keys: frozenset[str] = field(default_factory=frozenset)
    chaos_error_rate: float = 0.0
    enable_api_docs: bool = True
    config_errors: tuple[str, ...] = ()

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)

    @property
    def is_valid(self) -> bool:
        return not self.config_errors


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build Settings from the environment, collecting (not raising) errors."""
    env = dict(os.environ if env is None else env)
    errors: list[str] = []
    defaults = Settings()

    def parse(parser, name: str, default, *args):
        raw = env.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return parser(name, raw.strip(), *args)
        except ConfigError as exc:
            errors.append(str(exc))
            return default

    max_amount = parse(_decimal, "MAX_TRANSACTION_AMOUNT", defaults.max_transaction_amount)
    review = parse(_decimal, "REVIEW_THRESHOLD", defaults.review_threshold)
    if review > max_amount:
        errors.append("REVIEW_THRESHOLD must not exceed MAX_TRANSACTION_AMOUNT")

    currencies = _csv(env.get("ALLOWED_CURRENCIES", DEFAULT_CURRENCIES))
    if not currencies:
        errors.append("ALLOWED_CURRENCIES must list at least one currency")
    if any(len(code) != 3 or not code.isalpha() for code in currencies):
        errors.append("ALLOWED_CURRENCIES must contain ISO 4217 three-letter codes")

    blocked = _csv(env.get("BLOCKED_COUNTRIES", "KP,IR"))
    if any(len(code) != 2 or not code.isalpha() for code in blocked):
        errors.append("BLOCKED_COUNTRIES must contain ISO 3166 two-letter codes")

    return Settings(
        environment=env.get("APP_ENV", defaults.environment),
        log_level=env.get("LOG_LEVEL", defaults.log_level).upper(),
        max_transaction_amount=max_amount,
        review_threshold=review,
        allowed_currencies=currencies,
        blocked_countries=blocked,
        duplicate_window_seconds=parse(
            _int, "DUPLICATE_WINDOW_SECONDS", defaults.duplicate_window_seconds, 0
        ),
        api_keys=frozenset(k.strip() for k in env.get("API_KEYS", "").split(",") if k.strip()),
        chaos_error_rate=parse(_float_fraction, "CHAOS_ERROR_RATE", defaults.chaos_error_rate),
        enable_api_docs=env.get("ENABLE_API_DOCS", "true").strip().lower() in {"1", "true", "yes"},
        config_errors=tuple(errors),
    )
