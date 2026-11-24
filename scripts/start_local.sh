#!/usr/bin/env bash
set -euo pipefail

# Hakochuu 本地 Mac 一键启动脚本（自动建立 SSH 隧道 + 启动 Vite 前端，Ctrl+C 一键清理退出）
# 用法：
#   ./scripts/start_local.sh               # 自动连最近一次服务器隧道 + 启动前端
#   ./scripts/start_local.sh --local-only  # 不连远端，直接在本地启动 Mock 后端 + 前端

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HISTORY_FILE="${REPO_ROOT}/.hakochuu_server_history.json"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

BG_PID=""

cleanup() {
  if [[ -n "$BG_PID" ]] && kill -0 "$BG_PID" 2>/dev/null; then
    echo ""
    echo "正在关闭后台进程 (PID: $BG_PID)..."
    kill -15 "$BG_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ "${1:-}" == "--local-only" ]]; then
  echo "=== 本地纯单机模式：启动本地 Mock 后端 (23333) + 前端 (5173) ==="
  (
    cd "${REPO_ROOT}/backend"
    exec "${REPO_ROOT}/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 23333 --reload
  ) &
  BG_PID=$!
else
  TARGET_HOST=""
  if [[ -f "$HISTORY_FILE" ]]; then
    TARGET_HOST="$("$PYTHON_BIN" -c '
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    if data:
        print(data[0].get("host", ""))
except Exception:
    pass
' "$HISTORY_FILE")"
  fi

  if [[ -z "$TARGET_HOST" ]]; then
    echo "暂无历史服务器记录，请先运行 ./scripts/sync_server.sh 或使用 --local-only。" >&2
    exit 1
  fi

  echo "=== 正在后台建立 SSH 隧道 -> ${TARGET_HOST} (23333/11434/23334/23335/23336) ==="
  ssh -N \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L 23333:127.0.0.1:23333 \
    -L 11434:127.0.0.1:11434 \
    -L 23334:127.0.0.1:23334 \
    -L 23335:127.0.0.1:23335 \
    -L 23336:127.0.0.1:23336 \
    "$TARGET_HOST" &
  BG_PID=$!
  sleep 1
fi

echo "=== 正在启动 Vite 前端 (http://localhost:5173) ==="
cd "${REPO_ROOT}/frontend"
npm run dev
