#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

"$PYTHON" -m quant_lab.cli init
"$PYTHON" -m quant_lab.cli new-run --run-type weekly

echo "每周运行清单已创建。参数搜索将在策略、数据和计算预算确定后启用。"
