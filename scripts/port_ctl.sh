#!/usr/bin/env bash
set -euo pipefail

# Hakochuu 核心端口查看与关闭工具（支持本地 Mac 与远端 GPU 服务器）
# 核心端口：23333 (FastAPI), 11434 (LLM), 23334 (TTS), 23335 (Seed-VC), 23336 (ASR)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HISTORY_FILE="${REPO_ROOT}/.hakochuu_server_history.json"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

PORTS=(23333 11434 23334 23335 23336)

port_label() {
  case "$1" in
    23333) echo "FastAPI 网关" ;;
    11434) echo "LLM (Ollama/vLLM)" ;;
    23334) echo "TTS (CosyVoice/Qwen3-TTS)" ;;
    23335) echo "VC (Seed-VC)" ;;
    23336) echo "ASR (SenseVoice/Whisper)" ;;
    *)     echo "未知服务" ;;
  esac
}

pick_remote_host() {
  local hosts=()
  if [[ -f "$HISTORY_FILE" ]]; then
    while IFS= read -r line; do
      [[ -n "$line" ]] && hosts+=("$line")
    done < <("$PYTHON_BIN" -c '
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    seen = set()
    for item in data:
        h = item.get("host", "").strip()
        if h and h not in seen:
            seen.add(h)
            print(h)
except Exception:
    pass
' "$HISTORY_FILE")
  fi

  if [[ ${#hosts[@]} -gt 0 ]]; then
    echo "=== 历史服务器列表 ===" >&2
    local i=1
    for h in "${hosts[@]}"; do
      echo "  [$i] $h" >&2
      ((i++))
    done
    echo "  [0] 手动输入其他 SSH Host" >&2
    local choice
    read -r -p "请选择服务器编号 [默认 1]: " choice
    choice="${choice:-1}"
    if [[ "$choice" == "0" ]]; then
      read -r -p "请输入 SSH Host 或 user@ip: " manual_host
      echo "$manual_host"
      return
    fi
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#hosts[@]} )); then
      echo "${hosts[$((choice - 1))]}"
      return
    fi
    echo "无效编号" >&2
    exit 1
  else
    local manual_host
    read -r -p "未找到连接历史，请输入 SSH Host 或 user@ip: " manual_host
    echo "$manual_host"
  fi
}

show_local_ports() {
  echo ""
  echo "=== 本地 Mac 核心端口监听状态 ==="
  for p in "${PORTS[@]}"; do
    local info
    info="$(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {printf "%s(PID:%s) ", $1, $2}')"
    if [[ -n "$info" ]]; then
      printf "  %-6s %-26s -> 占用: %s\n" "$p" "($(port_label "$p"))" "$info"
    else
      printf "  %-6s %-26s -> 空闲\n" "$p" "($(port_label "$p"))"
    fi
  done
}

show_remote_ports() {
  local host="$1"
  echo ""
  echo "=== 远端服务器 ($host) 核心端口监听状态 ==="
  ssh "$host" 'bash -s' << 'EOF'
for p in 23333 11434 23334 23335 23336; do
  case "$p" in
    23333) label="FastAPI 网关" ;;
    11434) label="LLM (Ollama/vLLM)" ;;
    23334) label="TTS (CosyVoice/Qwen3-TTS)" ;;
    23335) label="VC (Seed-VC)" ;;
    23336) label="ASR (SenseVoice/Whisper)" ;;
  esac
  info=""
  if command -v ss >/dev/null 2>&1; then
    info="$(ss -lntp "sport = :$p" 2>/dev/null | awk 'NR>1 {print $NF}' | tr '\n' ' ')"
  elif command -v lsof >/dev/null 2>&1; then
    info="$(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {printf "%s(PID:%s) ", $1, $2}')"
  fi
  if [[ -n "$info" ]]; then
    printf "  %-6s (%-25s) -> 占用: %s\n" "$p" "$label" "$info"
  else
    printf "  %-6s (%-25s) -> 空闲\n" "$p" "$label"
  fi
done
EOF
}

kill_local_port() {
  local port="$1"
  local pids
  pids="$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    echo "本地端口 $port 当前未被占用。"
    return
  fi
  echo "正在优雅关闭本地端口 $port (PID: $pids)..."
  # shellcheck disable=SC2086
  kill -15 $pids 2>/dev/null || true
  sleep 1
  local remain
  remain="$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$remain" ]]; then
    read -r -p "进程 ($remain) 仍在运行，是否强制 kill -9？[y/N]: " force
    if [[ "$force" =~ ^[Yy]$ ]]; then
      # shellcheck disable=SC2086
      kill -9 $remain 2>/dev/null || true
    fi
  fi
  echo "本地端口 $port 处理完成。"
}

kill_remote_port() {
  local host="$1"
  local port="$2"
  echo "正在关闭远端 ($host) 端口 $port..."
  ssh -t "$host" "
    if command -v fuser >/dev/null 2>&1; then
      fuser -k -TERM ${port}/tcp 2>/dev/null || true
      sleep 1
      fuser -k -KILL ${port}/tcp 2>/dev/null || true
    elif command -v lsof >/dev/null 2>&1; then
      pids=\$(lsof -t -iTCP:${port} -sTCP:LISTEN 2>/dev/null || true)
      if [[ -n \"\$pids\" ]]; then
        kill -15 \$pids 2>/dev/null || true
        sleep 1
        kill -9 \$pids 2>/dev/null || true
      fi
    else
      echo '远端缺少 fuser 与 lsof，请手动 ss -lntp 查看 PID 后 kill。'
      exit 1
    fi
  "
  echo "远端端口 $port 处理完成。"
}

main() {
  echo "=== Hakochuu 端口管理工具 ==="
  echo "  [1] 查看并管理远端服务端端口"
  echo "  [2] 查看并管理本地 Mac 端口"
  echo "  [3] 同时查看本地与远端状态"
  read -r -p "请选择模式 [默认 1]: " mode
  mode="${mode:-1}"

  local target_host=""
  if [[ "$mode" == "1" || "$mode" == "3" ]]; then
    target_host="$(pick_remote_host)"
    [[ -n "$target_host" ]] || { echo "未指定服务器，退出。"; exit 1; }
  fi

  case "$mode" in
    1)
      show_remote_ports "$target_host"
      read -r -p $'\n请输入要关闭的远端端口号（直接回车退出）: ' port
      if [[ -n "$port" ]]; then
        kill_remote_port "$target_host" "$port"
        show_remote_ports "$target_host"
      fi
      ;;
    2)
      show_local_ports
      read -r -p $'\n请输入要关闭的本地端口号（直接回车退出）: ' port
      if [[ -n "$port" ]]; then
        kill_local_port "$port"
        show_local_ports
      fi
      ;;
    3)
      show_local_ports
      show_remote_ports "$target_host"
      ;;
    *)
      echo "无效模式" >&2
      exit 1
      ;;
  esac
}

main "$@"
