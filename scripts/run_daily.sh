#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

"$PYTHON" -m quant_lab.cli init
"$PYTHON" -m quant_lab.cli new-run --run-type daily

echo "每日运行清单已创建。尚未配置行情源和策略，因此没有执行回测。"
