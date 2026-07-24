#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

"$ROOT/scripts/check-standard.sh"
(cd "$ROOT/apps/web" && npm run build -- --webpack)
"$ROOT/scripts/doctor.sh"
"$ROOT/.venv/bin/python" -m pytest -q \
  "$ROOT/tests/test_agent_manifest.py" \
  "$ROOT/tests/test_batch_research.py" \
  "$ROOT/tests/test_safety_wrapper.py"
git -C "$ROOT" diff --check
