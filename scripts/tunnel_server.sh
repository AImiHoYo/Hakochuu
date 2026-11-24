#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

exec "$PYTHON_BIN" "${SCRIPT_DIR}/server_cli.py" --action tunnel "$@"
