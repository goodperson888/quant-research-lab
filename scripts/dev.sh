#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "未找到项目 Python 环境。请先运行 ./scripts/bootstrap.sh。" >&2
    exit 1
fi

exec "$PYTHON" "$ROOT/scripts/dev_manager.py" "$@"
