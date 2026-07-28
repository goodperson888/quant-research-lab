#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${QUANT_LAB_API_PORT:-8000}"
WEB_PORT="${QUANT_LAB_WEB_PORT:-3000}"
API_PID=""
WEB_PID=""

cleanup() {
    trap - EXIT INT TERM HUP
    for pid in "$API_PID" "$WEB_PID"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    for pid in "$API_PID" "$WEB_PID"; do
        if [[ -n "$pid" ]]; then
            wait "$pid" 2>/dev/null || true
        fi
    done
}

stop_all() {
    exit 130
}

trap cleanup EXIT
trap stop_all INT TERM HUP

echo "正在启动 Quant Research Lab..."
echo "Web Studio: http://127.0.0.1:${WEB_PORT}/studio"
echo "API health: http://127.0.0.1:${API_PORT}/health"
echo "按 Ctrl+C 可同时停止 API 和 Web。"

QUANT_LAB_API_PORT="$API_PORT" QUANT_LAB_WEB_PORT="$WEB_PORT" \
    "$ROOT/scripts/dev-api.sh" &
API_PID=$!

NEXT_PUBLIC_QUANT_LAB_API_URL="http://127.0.0.1:${API_PORT}" \
    "$ROOT/scripts/dev-web.sh" --port "$WEB_PORT" &
WEB_PID=$!

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
    sleep 1
done

status=0
if ! kill -0 "$API_PID" 2>/dev/null; then
    wait "$API_PID" || status=$?
    echo "API 已停止，正在关闭 Web。" >&2
else
    wait "$WEB_PID" || status=$?
    echo "Web 已停止，正在关闭 API。" >&2
fi
exit "$status"
