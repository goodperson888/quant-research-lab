#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

FREQTRADE="$ROOT/.venv/bin/freqtrade"
if [[ ! -x "$FREQTRADE" ]]; then
  echo "Freqtrade 尚未安装：请先运行 ./scripts/bootstrap.sh"
  exit 2
fi

for argument in "$@"; do
  if [[ "$argument" == "trade" ]]; then
    echo "安全门禁：本工程默认拒绝启动 Freqtrade trade 循环。"
    echo "历史回测和数据工具可用；模拟盘需先审核配置并由用户明确启用。"
    exit 3
  fi
done

exec "$FREQTRADE" "$@"
