"""Tests for the AI root-cause analyser (LLM calls are mocked)."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rca"))
import analyze

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ redaction
@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("key AKIAIOSFODNN7EXAMPLE used", "AKIAIOSFODNN7EXAMPLE"),
        ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCY", "wJalrXUtnFEMI"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456", "abcdefghijklmnop"),
        ("password=hunter2-super-secret", "hunter2"),
        ("--from-literal=API_KEYS=live-key-123", "live-key-123"),
        ("token: ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghij"),
        ("hook https://hooks.slack.com/services/T000/B000/XXXX", "T000/B000"),
        ("card 4111 1111 1111 1111 declined", "4111 1111 1111"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "eyJhbGci"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
    ],
)
def test_secrets_are_redacted(raw, leaked):
    assert leaked not in analyze.redact(raw)


def test_redaction_keeps_useful_context():
    text = analyze.redact("FAILED tests/test_api.py::test_x - assert 422 == 200")
    assert text == "FAILED tests/test_api.py::test_x - assert 422 == 200"


# ------------------------------------------------------------------ classification
@pytest.mark.parametrize(
    ("fixture", "category", "stage"),
    [
        ("failed-test.log", "TEST_FAILURE", "Unit & Integration Tests"),
        ("vulnerable-dependency.log", "VULNERABLE_DEPENDENCY", "Dependency Scan"),
        ("rollout-failed.log", "ROLLOUT_FAILED", "Deploy to EKS"),
        ("secret-leak.log", "SECRET_DETECTED", "Secret Scan"),
    ],
)
def test_known_failures_are_classified(fixture, category, stage):
    diagnosis = analyze.classify(load(fixture))
    assert diagnosis.category == category
    assert diagnosis.failing_stage == stage
    assert diagnosis.remediation
    assert diagnosis.confidence >= 0.8


def test_passing_image_scan_is_not_mistaken_for_a_vulnerability():
    # rollout-failed.log contains "Total: 0 (HIGH: 0, CRITICAL: 0)" from a passing scan.
    assert analyze.classify(load("rollout-failed.log")).category != "IMAGE_VULNERABILITY"


def test_vulnerability_evidence_lists_package_and_fix():
    evidence = analyze.classify(load("vulnerable-dependency.log")).evidence
    assert "requests 2.19.1 -> fixed in 2.20.0 (CVE-2018-18074, HIGH)" in evidence


def test_unknown_failure_falls_back_with_low_confidence():
    diagnosis = analyze.classify("[Pipeline] { (Build)\nsomething odd: error 42\n")
    assert diagnosis.category == "UNKNOWN"
    assert diagnosis.confidence < 0.5
    assert diagnosis.failing_stage == "Build"

    # Jenkins also opens every later stage it skips; those must not be blamed.
    skipped = (
        "[Pipeline] { (Build)\nerror: Failed to create virtual environment\n"
        '[Pipeline] { (Lint)\nStage "Lint" skipped due to earlier failure(s)\n'
        '[Pipeline] { (Smoke Test)\nStage "Smoke Test" skipped due to earlier failure(s)\n'
        "Failed in branch SAST - Bandit\n"
    )
    diagnosis = analyze.classify(skipped)
    assert diagnosis.failing_stage == "Build"
    assert diagnosis.evidence == ["error: Failed to create virtual environment"]


def test_error_excerpt_is_bounded():
    log = "\n".join(f"line {i} error happened" for i in range(10_000))
    assert len(analyze.error_excerpt(log, max_chars=500)) <= 500


# ------------------------------------------------------------------ LLM integration
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def llm_reply(payload: dict) -> FakeResponse:
    body = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    return FakeResponse(json.dumps(body).encode())


def test_llm_result_is_used_when_available(monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout):
        sent["body"] = json.loads(request.data)
        sent["headers"] = dict(request.header_items())
        return llm_reply(
            {
                "category": "TEST_FAILURE",
                "failing_stage": "Unit & Integration Tests",
                "root_cause": "The approval rule now rejects SGD transactions.",
                "evidence": ["assert 422 == 200"],
                "remediation": ["Revert the currency change"],
                "confidence": 0.9,
                "needs_human": True,
            }
        )

    monkeypatch.setattr(analyze.urllib.request, "urlopen", fake_urlopen)
    diagnosis, note = analyze.analyse(load("failed-test.log"), api_key="sk-test", model="m")
    assert diagnosis.source == "llm:m"
    assert diagnosis.root_cause.startswith("The approval rule")
    assert note == ""
    # Only the redacted excerpt is sent, with the rule-based hint.
    prompt = sent["body"]["messages"][0]["content"]
    assert "TEST_FAILURE" in prompt
    assert "<log>" in prompt


def test_prompt_never_contains_secrets(monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout):
        sent["body"] = request.data.decode()
        return llm_reply({"root_cause": "x", "confidence": 0.5})

    monkeypatch.setattr(analyze.urllib.request, "urlopen", fake_urlopen)
    log = load("failed-test.log") + "\nexport AWS key AKIAIOSFODNN7EXAMPLE error\n"
    analyze.analyse(log, api_key="sk-test")
    assert "AKIAIOSFODNN7EXAMPLE" not in sent["body"]


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("no network"),
        TimeoutError(),
        ValueError("bad json"),
    ],
)
def test_llm_failure_falls_back_to_rules(monkeypatch, failure):
    def boom(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(analyze.urllib.request, "urlopen", boom)
    diagnosis, note = analyze.analyse(load("failed-test.log"), api_key="sk-test")
    assert diagnosis.source == "rules"
    assert diagnosis.category == "TEST_FAILURE"
    assert "fell back" in note


def test_llm_reply_without_json_falls_back(monkeypatch):
    monkeypatch.setattr(
        analyze.urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse(json.dumps({"content": [{"text": "sorry"}]}).encode()),
    )
    diagnosis, _ = analyze.analyse(load("failed-test.log"), api_key="sk-test")
    assert diagnosis.source == "rules"


@pytest.mark.parametrize("key", [None, "", "not-configured"])
def test_no_api_key_means_no_network_call(monkeypatch, key):
    def forbidden(*_a, **_k):
        raise AssertionError("must not call the LLM")

    monkeypatch.setattr(analyze.urllib.request, "urlopen", forbidden)
    diagnosis, note = analyze.analyse(load("secret-leak.log"), api_key=key)
    assert diagnosis.category == "SECRET_DETECTED"
    assert "rule-based" in note


# ------------------------------------------------------------------ CLI
def test_cli_writes_markdown_and_json(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    md, js = tmp_path / "rca.md", tmp_path / "rca.json"
    code = analyze.main(
        [
            "--log",
            str(FIXTURES / "vulnerable-dependency.log"),
            "--output",
            str(md),
            "--json",
            str(js),
        ]
    )
    assert code == 0
    assert "VULNERABLE_DEPENDENCY" in md.read_text()
    assert "advisory only" in md.read_text()
    assert json.loads(js.read_text())["category"] == "VULNERABLE_DEPENDENCY"
    assert "Suggested remediation" in capsys.readouterr().out
