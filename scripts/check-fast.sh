#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

"$ROOT/.venv/bin/python" -m compileall -q "$ROOT/src" "$ROOT/tests"
"$ROOT/.venv/bin/python" -m pytest -q \
  "$ROOT/tests/test_batch_research.py" \
  "$ROOT/tests/test_product_foundation.py" \
  "$ROOT/tests/test_safety_wrapper.py"
(cd "$ROOT/apps/web" && npm run typecheck)
