#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

exec "$ROOT/.venv/bin/uvicorn" quant_lab.interfaces.api.app:app \
  --host 127.0.0.1 \
  --port "${QUANT_LAB_API_PORT:-8000}" \
  "$@"
