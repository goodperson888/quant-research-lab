#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export QUANT_LAB_HOME="$ROOT"
export UV_CACHE_DIR="$ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$ROOT/.tools/python"
export UV_TOOL_DIR="$ROOT/.tools/uv-tools"
export UV_PROJECT_ENVIRONMENT="$ROOT/.venv"
export UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
export XDG_CACHE_HOME="$ROOT/.cache"
export PYTHONPYCACHEPREFIX="$ROOT/.cache/pycache"
export MPLCONFIGDIR="$ROOT/.cache/matplotlib"
export PATH="$ROOT/.tools/bin:$PATH"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# Stable local development ports shared by every start/stop/status command.
export QUANT_LAB_API_PORT="${QUANT_LAB_API_PORT:-8100}"
export QUANT_LAB_WEB_PORT="${QUANT_LAB_WEB_PORT:-3100}"
