#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"
cd "$ROOT/apps/web"

export NEXT_PUBLIC_QUANT_LAB_API_URL="${NEXT_PUBLIC_QUANT_LAB_API_URL:-http://127.0.0.1:${QUANT_LAB_API_PORT}}"
export WATCHPACK_POLLING="${WATCHPACK_POLLING:-true}"
exec npm run dev -- --hostname 127.0.0.1 "$@"
