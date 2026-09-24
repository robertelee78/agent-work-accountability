#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python "$ROOT/scripts/validate_pack.py"
"$ROOT/tests/test_install.sh"
python -m unittest "$ROOT/tests/test_validate_extraction.py"
python -m unittest "$ROOT/tests/test_inspect_adr.py"
