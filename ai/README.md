# AI-assisted root-cause analysis (prototype)

When the Jenkins pipeline fails, `rca/analyze.py` reads the console log and
produces a short diagnosis — failing stage, likely root cause, evidence and
suggested fix — which is attached to the failure e-mail and archived as
`reports/rca.md` / `reports/rca.json`.

```
console log ─► redact secrets ─► find failing stage + error window
            ─► rule-based classifier (16 known failure signatures)
            ─► optional LLM (Claude) reasoning over the redacted excerpt
            ─► Markdown + JSON  ─► Jenkins e-mail / build artifacts
```

| Mode | When | Behaviour |
|---|---|---|
| Rule-based | default, no API key | Deterministic regex signatures, works offline |
| LLM | `anthropic-api-key` Jenkins credential set | Model reasons over the redacted excerpt + rule hint, returns strict JSON |
| Fallback | LLM error / timeout / bad JSON | Silently returns the rule-based result |

Guardrails: advisory only (never acts), secrets and card numbers are redacted
before anything leaves Jenkins, only a ≤12 KB excerpt is sent, every claim must
cite log evidence, and it never recommends disabling a security gate.

```bash
python3 ai/rca/analyze.py --log ai/tests/fixtures/vulnerable-dependency.log
pytest ai/tests -q        # 29 tests, LLM calls mocked
```

See *AI use case* in `docs/design-document.md` for the full analysis.
