#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

# 无参数时进入交互模式；传入 --last 可直接增量补充上传新增/修改的文件到最近一次服务器
if [[ $# -eq 0 ]]; then
  exec "$PYTHON_BIN" "${SCRIPT_DIR}/server_cli.py" --action interactive
fi

exec "$PYTHON_BIN" "${SCRIPT_DIR}/server_cli.py" --action sync "$@"
