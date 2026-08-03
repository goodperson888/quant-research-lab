#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

exec "$ROOT/.venv/bin/python" -m quant_lab.agents.cli "$@"
