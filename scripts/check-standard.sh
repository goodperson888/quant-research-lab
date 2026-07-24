#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

"$ROOT/.venv/bin/python" -m pytest -q
"$ROOT/.venv/bin/python" "$ROOT/scripts/validate-configs.py"
SKILL_VALIDATOR="${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py"
if [[ ! -f "$SKILL_VALIDATOR" ]]; then
  echo "skill validator not found: set CODEX_HOME or install skill-creator" >&2
  exit 1
fi
"$ROOT/.venv/bin/python" "$SKILL_VALIDATOR" \
  "$ROOT/.agents/skills/quant-strategy-research"
(cd "$ROOT/apps/web" && npm run lint && npm run typecheck)
