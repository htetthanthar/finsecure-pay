#!/usr/bin/env bash
# Substitute ${VAR} placeholders in a file with environment variable values
# (portable replacement for envsubst; only uses python3).
# Fails if any referenced variable is unset, so nothing is rendered half-empty.
set -euo pipefail

python3 - "$1" <<'EOF'
import os, re, sys
text = open(sys.argv[1]).read()
missing = sorted({m for m in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", text) if m not in os.environ})
if missing:
    sys.exit(f"render.sh: missing environment variables: {', '.join(missing)}")
sys.stdout.write(re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", lambda m: os.environ[m.group(1)], text))
EOF
