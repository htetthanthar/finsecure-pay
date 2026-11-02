#!/usr/bin/env python3
"""AI-assisted root-cause analysis (RCA) for failed CI/CD pipeline runs.

Pipeline:  console log -> redact secrets -> extract failing stage + error
window -> rule-based classifier -> (optional) LLM analysis -> Markdown/JSON.

Design principles (see docs/design-document.md, "AI use case"):
  * Advisory only - it never retries, rolls back or changes anything.
  * Data minimisation - only a redacted excerpt (not the whole log) is ever
    sent to the LLM, and only when an API key is configured.
  * Graceful degradation - any LLM error falls back to the deterministic
    rule-based result, so a failed build always gets *some* diagnosis.

Standard library only, so it runs on the Jenkins controller without a venv.

Usage:
  python3 ai/rca/analyze.py --log console.log --output rca.md --json rca.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("RCA_MODEL", "claude-haiku-5-5")
MAX_EXCERPT_CHARS = 12_000
DISABLED_KEY_VALUES = {"", "not-configured", "none", "changeme"}

# --------------------------------------------------------------------------- redaction
REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
    (re.compile(r"(?i)(aws_secret_access_key|secret_access_key)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED_JWT]",
    ),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 [REDACTED]"),
    (
        re.compile(
            r"\b(sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|dckr_pat_[A-Za-z0-9_-]{10,}"
            r"|xox[abpr]-[A-Za-z0-9-]{10,}|squ_[A-Za-z0-9]{20,})\b"
        ),
        "[REDACTED_TOKEN]",
    ),
    (re.compile(r"https://hooks\.slack\.com/services/\S+"), "[REDACTED_SLACK_WEBHOOK]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|token|api[_-]?keys?|secret)(\s*[=:]\s*)(?!\[REDACTED)"
            r"(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"\b(?:\d[ -]?){12,18}(\d{4})\b"), r"[REDACTED_PAN_****\1]"),
]


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


# --------------------------------------------------------------------------- log parsing
STAGE_PATTERN = re.compile(r"\[Pipeline\] \{ \((?P<stage>[^)]+)\)")
SKIPPED_PATTERN = re.compile(r'Stage "(?P<stage>[^"]+)" skipped due to')
# Jenkins bookkeeping that mentions "failure" without being the failure itself.
NOISE_PATTERN = re.compile(r'(Stage "[^"]+" skipped due to|Failed in branch |no leaks found)')
ERROR_HINTS = re.compile(
    r"(?i)(error|failed|failure|exception|traceback|fatal|denied|forbidden|timed out|"
    r"unauthori[sz]ed|critical|high:|leaks found|exit code [1-9]|script returned exit code)"
)


def failing_stage(log: str) -> str | None:
    """Last stage that actually ran (Jenkins prints `{ (Stage)` even for skipped ones)."""
    stages = STAGE_PATTERN.findall(log)
    ignored = {"Declarative: Post Actions", "Declarative: Checkout SCM"}
    ignored |= set(SKIPPED_PATTERN.findall(log))
    for stage in reversed(stages):
        # Parallel stages are opened twice: "{ (Branch: Name)" and "{ (Name)".
        if stage.removeprefix("Branch: ") not in ignored:
            return stage
    return None


def error_excerpt(log: str, context: int = 3, max_chars: int = MAX_EXCERPT_CHARS) -> str:
    """Lines around error hints, newest first priority, capped in size."""
    lines = log.splitlines()
    keep: set[int] = set()
    for index, line in enumerate(lines):
        if ERROR_HINTS.search(line):
            keep.update(range(max(0, index - context), min(len(lines), index + context + 1)))
    selected = [lines[i] for i in sorted(keep)] or lines[-60:]
    excerpt = "\n".join(selected)
    return excerpt[-max_chars:]


# --------------------------------------------------------------------------- rules
@dataclass
class Diagnosis:
    category: str
    root_cause: str
    remediation: list[str]
    confidence: float
    evidence: list[str] = field(default_factory=list)
    failing_stage: str | None = None
    source: str = "rules"
    needs_human: bool = True


@dataclass
class Rule:
    category: str
    pattern: re.Pattern[str]
    root_cause: str
    remediation: list[str]
    confidence: float


# Trivy prints "<target>" / "=====" / "Total: N (HIGH: x, CRITICAL: y)". Anchoring on
# that layout stops a passing scan in one stage matching a finding in another.
TRIVY_HEADER = (
    r"(?:{target})[^\n]*\n[^\n]*=+[^\n]*\n[^\n]*"
    r"Total: \d+ \([^)\n]*(?:HIGH|CRITICAL): [1-9][^\n]*"
)
TRIVY_ROW = re.compile(
    r"│\s*(?P<pkg>[\w.-]+)\s*│\s*(?P<cve>CVE-\d{4}-\d+)\s*│\s*(?P<sev>HIGH|CRITICAL)\s*│"
    r"[^│]*│\s*(?P<installed>[^│\s]+)\s*│\s*(?P<fixed>[^│\s]*)\s*│"
)

RULES: list[Rule] = [
    Rule(
        "SECRET_DETECTED",
        re.compile(r"(?i)leaks found: [1-9]"),
        "Gitleaks found a credential or key committed to the repository.",
        [
            "Revoke/rotate the exposed credential immediately - treat it as compromised.",
            "Remove it from the code and load it from Jenkins credentials or a Kubernetes Secret.",
            "Rewrite history (git filter-repo) if the commit was pushed to a shared remote.",
        ],
        0.95,
    ),
    Rule(
        "TEST_FAILURE",
        re.compile(r"(FAILED tests/\S+|=+ [^\n]*\d+ failed)"),
        "One or more unit/integration tests failed, so the pipeline stopped before packaging.",
        [
            "Run `pytest -x -vv` locally on the same commit to reproduce.",
            "Check whether the change altered business rules the tests encode.",
            "Fix the code or update the test only if the requirement genuinely changed.",
        ],
        0.9,
    ),
    Rule(
        "COVERAGE_GATE",
        re.compile(r"(?i)required test coverage of [\d.]+% not reached"),
        "Tests passed but coverage fell below the 90% quality gate.",
        ["Add tests for the new/changed code paths shown as 'Missing' in the coverage report."],
        0.9,
    ),
    Rule(
        "LINT_FAILURE",
        re.compile(r"(Found \d+ errors?\.|would be reformatted|Would reformat)"),
        "Ruff lint or formatting check failed.",
        ["Run `ruff check --fix . && ruff format .` locally and commit the result."],
        0.85,
    ),
    Rule(
        "SAST_FINDING",
        re.compile(r"(?s)>> Issue: \[B\d+.*?Severity: High"),
        "Bandit static analysis found a HIGH severity security issue in the application code.",
        [
            "Open reports/bandit.sarif, fix the flagged code (e.g. injection, weak crypto).",
            "Only suppress with `# nosec <ID>` plus a written justification if it is a "
            "false positive.",
        ],
        0.85,
    ),
    Rule(
        "QUALITY_GATE",
        re.compile(r"(?i)quality gate (status: )?fail"),
        "The SonarQube quality gate failed (new bugs, vulnerabilities, hotspots or coverage).",
        ["Open the SonarQube project page linked in the log and address new issues on new code."],
        0.85,
    ),
    Rule(
        "VULNERABLE_DEPENDENCY",
        re.compile(TRIVY_HEADER.format(target=r"requirements\.txt \(pip\)|\(python-pkg\)")),
        "A runtime dependency has a known HIGH/CRITICAL vulnerability (CVE) - the security gate "
        "blocked the release.",
        [
            "Upgrade the package to the 'Fixed Version' Trivy reports, in requirements.in.",
            "Regenerate the lock file: "
            "pip-compile --generate-hashes -o requirements.txt requirements.in",
            "Re-run the pipeline; if no fix exists, assess exploitability and record a time-boxed "
            "exception in .trivyignore with a justification.",
        ],
        0.85,
    ),
    Rule(
        "IMAGE_VULNERABILITY",
        re.compile(TRIVY_HEADER.format(target=r"\((?:debian|alpine|ubuntu)[^)\n]*\)")),
        "The container base image contains a HIGH/CRITICAL vulnerability with an available fix.",
        ["Re-pin the base images to the latest digest (scripts/pin-base-images.sh) and rebuild."],
        0.8,
    ),
    Rule(
        "IAC_MISCONFIGURATION",
        re.compile(r"Failures: \d+ \([^)\n]*CRITICAL: [1-9]"),
        "Trivy found a CRITICAL misconfiguration in the Dockerfile or Kubernetes manifests.",
        ["Read the AVD-* rule IDs in the log; fix the manifest (e.g. privileged, hostPath)."],
        0.8,
    ),
    Rule(
        "REGISTRY_AUTH",
        re.compile(
            r"(?i)(unauthorized: incorrect username or password|denied: requested access"
            r"|authentication required)"
        ),
        "Docker Hub rejected the push credentials.",
        [
            "Regenerate the Docker Hub access token and update the dockerhub-credentials entry.",
            "Check DOCKERHUB_USER in the Jenkinsfile matches the token's account.",
        ],
        0.9,
    ),
    Rule(
        "IMAGE_PULL",
        re.compile(r"(ImagePullBackOff|ErrImagePull)"),
        "Kubernetes could not pull the new image (wrong tag, private repo or registry outage).",
        [
            "Confirm the tag exists on Docker Hub and the Push stage succeeded.",
            "The pipeline rolled back automatically; the previous version is still serving.",
        ],
        0.85,
    ),
    Rule(
        "ROLLOUT_FAILED",
        re.compile(
            r"(?i)(exceeded its progress deadline|timed out waiting for the condition"
            r"|failed health checks and was rolled back)"
        ),
        "The new pods never became Ready, so the rollout timed out and was rolled back.",
        [
            "Check pod events and /readyz output: kubectl -n finsecure describe pod <pod>.",
            "Common causes: invalid ConfigMap value, missing Secret key, crash on startup.",
            "Confirm the rollback: kubectl -n finsecure rollout history deployment/finsecure-pay.",
        ],
        0.85,
    ),
    Rule(
        "K8S_RBAC",
        re.compile(r"is forbidden: User \"[^\"]+\" cannot"),
        "The Jenkins deployer identity lacks a Kubernetes permission for this action.",
        ["Add only the missing verb/resource to k8s/cluster/rbac-deployer.yaml (least privilege)."],
        0.9,
    ),
    Rule(
        "CLOUD_AUTH",
        re.compile(
            r"(InvalidClientTokenId|ExpiredToken|SignatureDoesNotMatch|"
            r"Unable to locate credentials|AccessDeniedException)"
        ),
        "AWS rejected the deployer credentials.",
        [
            "Rotate the finsecure-jenkins-deployer access key and update the "
            "aws-deployer credential."
        ],
        0.85,
    ),
    Rule(
        "DEPENDENCY_LOCK",
        re.compile(r"(?i)(do not match the hashes|hash mismatch|No matching distribution found)"),
        "Dependency installation failed against the hash-locked requirements.",
        ["Regenerate the lock files with pip-compile --generate-hashes and commit them."],
        0.8,
    ),
    Rule(
        "SMOKE_TEST_FAILED",
        re.compile(r"FAIL .*(health|transaction|reachable)"),
        "The deployed service failed the post-deployment smoke test; it was rolled back.",
        [
            "Reproduce with scripts/smoke-test.sh against the ALB URL.",
            "Check logs in Grafana > Explore > Loki for the smoke-* request IDs.",
        ],
        0.8,
    ),
]


def classify(log: str) -> Diagnosis:
    stage = failing_stage(log)
    for rule in RULES:
        match = rule.pattern.search(log)
        if match:
            evidence = [line.strip() for line in match.group(0).splitlines() if line.strip()][-3:]
            evidence += [
                f"{m['pkg']} {m['installed']} -> fixed in {m['fixed'] or 'n/a'} "
                f"({m['cve']}, {m['sev']})"
                for m in TRIVY_ROW.finditer(log)
            ][:5]
            return Diagnosis(
                category=rule.category,
                root_cause=rule.root_cause,
                remediation=rule.remediation,
                confidence=rule.confidence,
                evidence=[redact(e)[:300] for e in evidence],
                failing_stage=stage,
            )
    tail = [
        line
        for line in log.splitlines()
        if ERROR_HINTS.search(line) and not NOISE_PATTERN.search(line)
    ][-3:]
    return Diagnosis(
        category="UNKNOWN",
        root_cause="No known failure signature matched; manual investigation needed.",
        remediation=["Read the console log around the last error lines below."],
        confidence=0.2,
        evidence=[redact(t)[:300] for t in tail],
        failing_stage=stage,
    )


# --------------------------------------------------------------------------- LLM
SYSTEM_PROMPT = """You are a senior DevOps/SRE engineer doing root-cause analysis of a failed \
Jenkins CI/CD pipeline for a fintech payment-validation microservice (Python/FastAPI, Docker, \
Kubernetes on AWS EKS, Trivy, Gitleaks, SonarQube).
You receive a REDACTED excerpt of the console log and a rule-based pre-classification.
Rules:
- Base every claim on the log excerpt. If evidence is insufficient, say so and lower confidence.
- Never invent CVE IDs, file names or versions that are not in the excerpt.
- Recommend safe, reversible fixes. Never suggest disabling a security gate as the fix.
- Reply with ONLY a JSON object with keys: category (string, UPPER_SNAKE_CASE), \
failing_stage (string or null), root_cause (string, <= 3 sentences), evidence (array of short \
quotes from the log), remediation (array of concrete steps), confidence (number 0-1), \
needs_human (boolean)."""


def llm_diagnose(excerpt: str, prior: Diagnosis, api_key: str, model: str) -> Diagnosis:
    user = (
        f"Rule-based pre-classification: {prior.category} "
        f"(confidence {prior.confidence}). Failing stage: {prior.failing_stage}.\n\n"
        f"Redacted log excerpt:\n<log>\n{excerpt}\n</log>"
    )
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 800,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
        }
    ).encode()
    request = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read())
    text = "".join(block.get("text", "") for block in payload.get("content", []))
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("LLM reply contained no JSON object")
    data = json.loads(match.group(0))
    confidence = float(data.get("confidence", 0.5))
    return Diagnosis(
        category=str(data.get("category", prior.category))[:60],
        root_cause=redact(str(data["root_cause"]))[:1000],
        remediation=[redact(str(step))[:400] for step in data.get("remediation", [])][:8],
        confidence=max(0.0, min(1.0, confidence)),
        evidence=[redact(str(e))[:300] for e in data.get("evidence", [])][:6],
        failing_stage=data.get("failing_stage") or prior.failing_stage,
        source=f"llm:{model}",
        needs_human=bool(data.get("needs_human", True)),
    )


# --------------------------------------------------------------------------- output
def to_markdown(diagnosis: Diagnosis, note: str = "") -> str:
    lines = [
        f"Category:      {diagnosis.category}",
        f"Failing stage: {diagnosis.failing_stage or 'unknown'}",
        f"Confidence:    {diagnosis.confidence:.0%}  (source: {diagnosis.source})",
        "",
        f"Likely root cause: {diagnosis.root_cause}",
        "",
        "Suggested remediation:",
        *[f"  {i}. {step}" for i, step in enumerate(diagnosis.remediation, 1)],
    ]
    if diagnosis.evidence:
        lines += ["", "Evidence from the log:", *[f"  > {e}" for e in diagnosis.evidence]]
    lines += ["", "AI-generated, advisory only - verify before acting."]
    if note:
        lines.append(note)
    return "\n".join(lines) + "\n"


def analyse(log_text: str, api_key: str | None = None, model: str = DEFAULT_MODEL):
    """Return (diagnosis, note). Never raises because of the LLM."""
    redacted = redact(log_text)
    prior = classify(redacted)
    if not api_key or api_key.strip().lower() in DISABLED_KEY_VALUES:
        return prior, "LLM not configured - rule-based analysis."
    try:
        return llm_diagnose(error_excerpt(redacted), prior, api_key, model), ""
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return prior, f"LLM unavailable ({type(exc).__name__}) - fell back to rule-based analysis."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI root-cause analysis for failed pipelines")
    parser.add_argument("--log", required=True, type=Path, help="Jenkins console log file")
    parser.add_argument("--output", type=Path, help="write Markdown summary here")
    parser.add_argument("--json", type=Path, help="write machine-readable result here")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    log_text = args.log.read_text(encoding="utf-8", errors="replace")
    diagnosis, note = analyse(log_text, os.environ.get("ANTHROPIC_API_KEY"), args.model)
    markdown = to_markdown(diagnosis, note)
    print(markdown)
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(asdict(diagnosis), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
