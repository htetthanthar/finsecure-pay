import json
import logging
import logging.config
import sys

from app.logging_config import JsonFormatter, build_logging_config, redact, request_id_var


def make_record(message, **kwargs):
    record = logging.LogRecord("finsecure.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_single_line_json_with_context():
    token = request_id_var.set("req-123")
    try:
        line = JsonFormatter(version="9.9.9").format(
            make_record("hello", fields={"decision": "APPROVED", "amount": "10"})
        )
    finally:
        request_id_var.reset(token)
    payload = json.loads(line)
    assert "\n" not in line
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-123"
    assert payload["version"] == "9.9.9"
    assert payload["decision"] == "APPROVED"


def test_card_numbers_are_redacted_everywhere():
    line = JsonFormatter().format(
        make_record("card 4111111111111111 used", fields={"note": "pan 4111 1111 1111 1111"})
    )
    assert "4111111111111111" not in line
    assert "4111 1111 1111 1111" not in line
    assert "****1111" in line


def test_exceptions_are_included():
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record("failed", exc_info=sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


def test_redact_leaves_short_numbers_alone():
    assert redact("order 12345 amount 100") == "order 12345 amount 100"


def test_logging_config_is_valid_dictconfig():
    config = build_logging_config("INFO", "1.0.0")
    logging.config.dictConfig(config)
    assert logging.getLogger("finsecure").handlers
