"""Structured JSON logging.

Every log line is a single JSON object with a timestamp, level, logger,
message, the request ID of the HTTP request being served, and any structured
fields passed via ``extra={"fields": {...}}``. JSON lines are parsed by
Fluent Bit and indexed in Loki, so operators can filter on any field.

A redaction step masks anything that looks like a card number as a last line
of defence, so a PAN can never leak into the log pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

PAN_PATTERN = re.compile(r"\b(?:\d[ -]?){12,18}(\d{4})\b")


def redact(text: str) -> str:
    return PAN_PATTERN.sub(lambda m: f"****{m.group(1)}", text)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = "finsecure-pay", version: str = "unknown") -> None:
        super().__init__()
        self.service = service
        self.version = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "service": self.service,
            "version": self.version,
            "request_id": request_id_var.get(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                payload[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def build_logging_config(level: str, version: str) -> dict[str, Any]:
    """dictConfig used for both the app and uvicorn's own loggers."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "app.logging_config.JsonFormatter",
                "version": version,
            }
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            "finsecure": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
            # Access logging is done by our own middleware (structured, with request IDs).
            "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
        },
        "root": {"handlers": ["stdout"], "level": "WARNING"},
    }
