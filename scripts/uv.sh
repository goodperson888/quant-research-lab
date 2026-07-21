#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

UV="$ROOT/.tools/bin/uv"
if [[ ! -x "$UV" ]]; then
  echo "项目内尚未安装 uv：$UV"
  exit 2
fi

exec "$UV" "$@"
