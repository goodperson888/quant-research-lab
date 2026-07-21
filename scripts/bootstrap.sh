#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/env.sh"

UV="$ROOT/.tools/bin/uv"
EXPECTED_UV_VERSION="0.11.29"
PYTHON_VERSION="$(tr -d '[:space:]' < "$ROOT/.python-version")"
if [[ ! -x "$UV" ]]; then
  echo "项目内尚未安装 uv：$UV"
  echo "先执行经过审核的 uv 安装步骤，再重新运行本脚本。"
  exit 2
fi

INSTALLED_UV_VERSION="$($UV --version | awk '{print $2}')"
if [[ "$INSTALLED_UV_VERSION" != "$EXPECTED_UV_VERSION" ]]; then
  echo "uv 版本不匹配：期望 $EXPECTED_UV_VERSION，实际 $INSTALLED_UV_VERSION"
  exit 3
fi

mkdir -p "$ROOT/.cache/uv" "$ROOT/.cache/matplotlib" "$ROOT/.tools/python" "$ROOT/.tools/uv-tools"
"$UV" python install "$PYTHON_VERSION" --no-bin
"$UV" sync --extra research --extra test --extra freqtrade --frozen --managed-python

echo "研究环境已安装到 $ROOT/.venv"
echo "Python、研究依赖和基础 Freqtrade 已按 uv.lock 同步。"
echo "未安装 Hyperopt/FreqAI/vectorbt；未下载行情；未连接交易账户。"
