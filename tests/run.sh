#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || {
  echo "Python 3 is required to run the skill-pack tests" >&2
  exit 1
}

"$PYTHON" "$ROOT/scripts/validate_pack.py"
"$ROOT/tests/test_install.sh"
"$PYTHON" -m unittest "$ROOT/tests/test_validate_extraction.py"
"$PYTHON" -m unittest "$ROOT/tests/test_inspect_adr.py"
"$PYTHON" -m unittest "$ROOT/tests/test_gh_account.py"
"$PYTHON" -m unittest "$ROOT/tests/test_reconcile_project.py"
