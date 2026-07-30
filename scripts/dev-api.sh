#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

reload_args=()
if [[ "${QUANT_LAB_API_RELOAD:-true}" != "false" ]]; then
  reload_args=(--reload --reload-dir "$ROOT/src")
fi

exec "$ROOT/.venv/bin/uvicorn" quant_lab.interfaces.api.app:app \
  --host 127.0.0.1 \
  --port "${QUANT_LAB_API_PORT:-8000}" \
  "${reload_args[@]}" \
  "$@"
