#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/apps/web"

export NEXT_PUBLIC_QUANT_LAB_API_URL="${NEXT_PUBLIC_QUANT_LAB_API_URL:-http://127.0.0.1:8000}"
exec npm run dev -- --hostname 127.0.0.1 "$@"
