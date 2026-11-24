#!/usr/bin/env bash
set -euo pipefail

# Hakochuu 服务端一键启动脚本（在 GPU 服务器项目根目录执行）
# 用法：
#   ./scripts/start_server.sh         # 前台启动 FastAPI + vLLM + ASR + TTS + VC（Ctrl+C 同时停止）
#   ./scripts/start_server.sh --bg    # 后台常驻，日志写入 storage/*.log
#   ./scripts/start_server.sh --stop  # 停止 FastAPI、vLLM、ASR、TTS 与 VC
#
# 跳过 vLLM：HAKOCHUU_LLM_MOCK=true 或 HAKOCHUU_START_VLLM=false
# 跳过 ASR： HAKOCHUU_ASR_MOCK=true 或 HAKOCHUU_START_ASR=false
# 跳过 TTS： HAKOCHUU_TTS_MOCK=true 或 HAKOCHUU_START_TTS=false
# 跳过 VC：  HAKOCHUU_VC_MOCK=true 或 HAKOCHUU_START_VC=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
export REPO_ROOT

STORAGE_DIR="${REPO_ROOT}/storage"
VLLM_LOG="${STORAGE_DIR}/vllm.log"
ASR_LOG="${STORAGE_DIR}/asr.log"
TTS_LOG="${STORAGE_DIR}/tts.log"
VC_LOG="${STORAGE_DIR}/vc.log"
UVICORN_LOG="${STORAGE_DIR}/uvicorn.log"
VLLM_PID_FILE="${STORAGE_DIR}/vllm.pid"
ASR_PID_FILE="${STORAGE_DIR}/asr.pid"
TTS_PID_FILE="${STORAGE_DIR}/tts.pid"
VC_PID_FILE="${STORAGE_DIR}/vc.pid"
UVICORN_PID_FILE="${STORAGE_DIR}/uvicorn.pid"

mkdir -p storage/characters storage/songs storage/uploads cache/hf cache/torch cache/audio_separator cache/tmp

if [[ ! -f ".env" && -f ".env.example" ]]; then
  echo "未检测到 .env，自动从 .env.example 复制..."
  cp .env.example .env
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${HAKOCHUU_HOST:-127.0.0.1}"
PORT="${HAKOCHUU_PORT:-23333}"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
UVICORN_BIN="${REPO_ROOT}/.venv/bin/uvicorn"
VLLM_BIN="${REPO_ROOT}/.venv/bin/vllm"

if [[ ! -x "$UVICORN_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "错误: 未找到服务端虚拟环境 $UVICORN_BIN，请先构建 .venv。" >&2
  exit 1
fi

should_run_vllm() {
  local llm_mock="${HAKOCHUU_LLM_MOCK:-}"
  local start_vllm="${HAKOCHUU_START_VLLM:-true}"
  if [[ "${llm_mock,,}" == "true" ]]; then
    return 1
  fi
  if [[ "${start_vllm,,}" == "false" ]]; then
    return 1
  fi
  return 0
}

should_run_asr() {
  local asr_mock="${HAKOCHUU_ASR_MOCK:-}"
  local start_asr="${HAKOCHUU_START_ASR:-true}"
  if [[ "${start_asr,,}" == "false" ]]; then
    return 1
  fi
  if [[ "${asr_mock,,}" == "true" ]]; then
    return 1
  fi
  if [[ -z "$asr_mock" && "${HAKOCHUU_MOCK_ENGINES:-true}" == "true" ]]; then
    return 1
  fi
  return 0
}

should_run_tts() {
  local tts_mock="${HAKOCHUU_TTS_MOCK:-}"
  local start_tts="${HAKOCHUU_START_TTS:-true}"
  if [[ "${start_tts,,}" == "false" ]]; then
    return 1
  fi
  if [[ "${tts_mock,,}" == "true" ]]; then
    return 1
  fi
  if [[ -z "$tts_mock" && "${HAKOCHUU_MOCK_ENGINES:-true}" == "true" ]]; then
    return 1
  fi
  return 0
}

should_run_vc() {
  local vc_mock="${HAKOCHUU_VC_MOCK:-}"
  local start_vc="${HAKOCHUU_START_VC:-true}"
  if [[ "${start_vc,,}" == "false" ]]; then
    return 1
  fi
  if [[ "${vc_mock,,}" == "true" ]]; then
    return 1
  fi
  if [[ -z "$vc_mock" && "${HAKOCHUU_MOCK_ENGINES:-true}" == "true" ]]; then
    return 1
  fi
  return 0
}

port_pids() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -lntp "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u
  elif command -v fuser >/dev/null 2>&1; then
    fuser "${port}/tcp" 2>/dev/null | tr -s ' ' '\n' | sed '/^$/d'
  fi
}

port_in_use() {
  local pids
  pids="$(port_pids "$1")"
  [[ -n "${pids}" ]]
}

kill_pid_tree() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  local i=0
  while (( i < 8 )) && kill -0 "$pid" 2>/dev/null; do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL -- -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
}

kill_port() {
  local port="$1"
  local pid
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    kill_pid_tree "$pid"
  done < <(port_pids "$port")
  if command -v fuser >/dev/null 2>&1; then
    fuser -k -TERM "${port}/tcp" >/dev/null 2>&1 || true
    sleep 1
    fuser -k -KILL "${port}/tcp" >/dev/null 2>&1 || true
  fi
}

stop_vllm() {
  if [[ -f "$VLLM_PID_FILE" ]]; then
    kill_pid_tree "$(cat "$VLLM_PID_FILE" 2>/dev/null || true)"
    rm -f "$VLLM_PID_FILE"
  fi
  pkill -TERM -f "${REPO_ROOT}/.venv/bin/vllm serve" >/dev/null 2>&1 || true
  sleep 1
  pkill -KILL -f "${REPO_ROOT}/.venv/bin/vllm serve" >/dev/null 2>&1 || true
  kill_port 11434
}

stop_asr() {
  if [[ -f "$ASR_PID_FILE" ]]; then
    kill_pid_tree "$(cat "$ASR_PID_FILE" 2>/dev/null || true)"
    rm -f "$ASR_PID_FILE"
  fi
  pkill -TERM -f "${REPO_ROOT}/.venv/bin/uvicorn app.services.asr_server:app" >/dev/null 2>&1 || true
  sleep 1
  pkill -KILL -f "${REPO_ROOT}/.venv/bin/uvicorn app.services.asr_server:app" >/dev/null 2>&1 || true
  kill_port 23336
}

stop_tts() {
  if [[ -f "$TTS_PID_FILE" ]]; then
    kill_pid_tree "$(cat "$TTS_PID_FILE" 2>/dev/null || true)"
    rm -f "$TTS_PID_FILE"
  fi
  pkill -TERM -f "${REPO_ROOT}/.venv/bin/uvicorn app.services.tts_server:app" >/dev/null 2>&1 || true
  sleep 1
  pkill -KILL -f "${REPO_ROOT}/.venv/bin/uvicorn app.services.tts_server:app" >/dev/null 2>&1 || true
  kill_port 23334
}

stop_vc() {
  if [[ -f "$VC_PID_FILE" ]]; then
    kill_pid_tree "$(cat "$VC_PID_FILE" 2>/dev/null || true)"
    rm -f "$VC_PID_FILE"
  fi
  pkill -TERM -f "${REPO_ROOT}/.venv/bin/uvicorn app.services.vc_server:app" >/dev/null 2>&1 || true
  sleep 1
  pkill -KILL -f "${REPO_ROOT}/.venv/bin/uvicorn app.services.vc_server:app" >/dev/null 2>&1 || true
  kill_port 23335
}

stop_uvicorn() {
  if [[ -f "$UVICORN_PID_FILE" ]]; then
    kill_pid_tree "$(cat "$UVICORN_PID_FILE" 2>/dev/null || true)"
    rm -f "$UVICORN_PID_FILE"
  fi
  pkill -TERM -f "${REPO_ROOT}/.venv/bin/uvicorn app.main:app" >/dev/null 2>&1 || true
  sleep 1
  pkill -KILL -f "${REPO_ROOT}/.venv/bin/uvicorn app.main:app" >/dev/null 2>&1 || true
  kill_port "$PORT"
}

load_vllm_config() {
  HAKOCHUU_LLM_MODEL="${HAKOCHUU_LLM_MODEL:-}" \
  HAKOCHUU_LLM_BASE_URL="${HAKOCHUU_LLM_BASE_URL:-}" \
  HAKOCHUU_VLLM_MAX_MODEL_LEN="${HAKOCHUU_VLLM_MAX_MODEL_LEN:-}" \
  "$PYTHON_BIN" - <<'PY'
import os, re
from pathlib import Path

root = Path(os.environ["REPO_ROOT"])
model_id = os.environ.get("HAKOCHUU_LLM_MODEL") or ""
try:
    import yaml
    manifest = yaml.safe_load((root / "shared/models_manifest.yaml").read_text(encoding="utf-8")) or {}
    llm = (manifest.get("models") or {}).get("llm") or {}
    if not model_id:
        model_id = str(llm.get("default_model_id") or "qwen2.5-social-3b-nb-chat")
    cfg_path = root / "shared" / "llm_models" / model_id / "model.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    deploy = cfg.get("deployment") or {}
    weights = deploy.get("weights_dir") or llm.get("weights_dir") or ""
    served = deploy.get("served_model_name") or llm.get("model_name") or model_id
    endpoint = os.environ.get("HAKOCHUU_LLM_BASE_URL") or deploy.get("endpoint") or llm.get("endpoint") or "http://127.0.0.1:11434/v1"
    yaml_max = int(deploy.get("max_context_length") or 1024)
except Exception:
    weights = "/path/to/pretrain_models/Qwen2.5-Social-3B-NB-Chat"
    served = model_id or "qwen2.5-social-3b-nb-chat"
    endpoint = os.environ.get("HAKOCHUU_LLM_BASE_URL") or "http://127.0.0.1:11434/v1"
    yaml_max = 1024

match = re.search(r"https?://([^:/]+):(\d+)", endpoint)
host = match.group(1) if match else "127.0.0.1"
port = match.group(2) if match else "11434"
if host in {"localhost", "0.0.0.0"}:
    host = "127.0.0.1"
env_max = os.environ.get("HAKOCHUU_VLLM_MAX_MODEL_LEN")
max_len = int(env_max) if env_max else max(yaml_max, 4096)
print(weights)
print(served)
print(host)
print(port)
print(max_len)
print(model_id or served)
PY
}

load_asr_config() {
  HAKOCHUU_ASR_ENGINE="${HAKOCHUU_ASR_ENGINE:-}" \
  HAKOCHUU_ASR_WEIGHTS_DIR="${HAKOCHUU_ASR_WEIGHTS_DIR:-}" \
  HAKOCHUU_ASR_SERVER_URL="${HAKOCHUU_ASR_SERVER_URL:-}" \
  "$PYTHON_BIN" - <<'PY'
import os, re
from pathlib import Path

root = Path(os.environ["REPO_ROOT"])
engine = os.environ.get("HAKOCHUU_ASR_ENGINE") or ""
weights = os.environ.get("HAKOCHUU_ASR_WEIGHTS_DIR") or ""
endpoint = os.environ.get("HAKOCHUU_ASR_SERVER_URL") or ""
try:
    import yaml
    manifest = yaml.safe_load((root / "shared/models_manifest.yaml").read_text(encoding="utf-8")) or {}
    asr = (manifest.get("models") or {}).get("asr") or {}
    if not engine:
        engine = str(asr.get("engine") or "sensevoice")
    if not weights:
        weights = str(asr.get("weights_dir") or "")
    if not endpoint:
        endpoint = str(asr.get("endpoint") or "http://127.0.0.1:23336")
except Exception:
    engine = engine or "sensevoice"
    weights = weights or "/path/to/pretrain_models/SenseVoiceSmall"
    endpoint = endpoint or "http://127.0.0.1:23336"

match = re.search(r"https?://([^:/]+):(\d+)", endpoint)
host = match.group(1) if match else "127.0.0.1"
port = match.group(2) if match else "23336"
if host in {"localhost", "0.0.0.0"}:
    host = "127.0.0.1"
print(engine)
print(weights)
print(host)
print(port)
PY
}

load_tts_config() {
  HAKOCHUU_TTS_ENGINE="${HAKOCHUU_TTS_ENGINE:-}" \
  HAKOCHUU_TTS_WEIGHTS_DIR="${HAKOCHUU_TTS_WEIGHTS_DIR:-}" \
  HAKOCHUU_TTS_SERVER_URL="${HAKOCHUU_TTS_SERVER_URL:-}" \
  HAKOCHUU_COSYVOICE_REPO_DIR="${HAKOCHUU_COSYVOICE_REPO_DIR:-}" \
  "$PYTHON_BIN" - <<'PY'
import os, re
from pathlib import Path

root = Path(os.environ["REPO_ROOT"])
engine = os.environ.get("HAKOCHUU_TTS_ENGINE") or ""
weights = os.environ.get("HAKOCHUU_TTS_WEIGHTS_DIR") or ""
endpoint = os.environ.get("HAKOCHUU_TTS_SERVER_URL") or ""
repo = os.environ.get("HAKOCHUU_COSYVOICE_REPO_DIR") or ""
try:
    import yaml
    manifest = yaml.safe_load((root / "shared/models_manifest.yaml").read_text(encoding="utf-8")) or {}
    tts = (manifest.get("models") or {}).get("tts") or {}
    if not engine:
        engine = str(tts.get("engine") or "cosyvoice3")
    if not weights:
        weights = str(tts.get("weights_dir") or "")
    if not endpoint:
        endpoint = str(tts.get("endpoint") or "http://127.0.0.1:23334")
except Exception:
    engine = engine or "cosyvoice3"
    weights = weights or "/path/to/pretrain_models/Fun-CosyVoice3-0.5B"
    endpoint = endpoint or "http://127.0.0.1:23334"

if not repo:
    candidate = Path("/path/to/projects/CosyVoice")
    repo = str(candidate) if candidate.is_dir() else ""

match = re.search(r"https?://([^:/]+):(\d+)", endpoint)
host = match.group(1) if match else "127.0.0.1"
port = match.group(2) if match else "23334"
if host in {"localhost", "0.0.0.0"}:
    host = "127.0.0.1"
print(engine)
print(weights)
print(host)
print(port)
print(repo)
PY
}

load_vc_config() {
  HAKOCHUU_VC_ENGINE="${HAKOCHUU_VC_ENGINE:-}" \
  HAKOCHUU_VC_WEIGHTS_DIR="${HAKOCHUU_VC_WEIGHTS_DIR:-}" \
  HAKOCHUU_VC_SERVER_URL="${HAKOCHUU_VC_SERVER_URL:-}" \
  HAKOCHUU_VC_REPO_DIR="${HAKOCHUU_VC_REPO_DIR:-}" \
  "$PYTHON_BIN" - <<'PY'
import os, re
from pathlib import Path

root = Path(os.environ["REPO_ROOT"])
engine = os.environ.get("HAKOCHUU_VC_ENGINE") or ""
weights = os.environ.get("HAKOCHUU_VC_WEIGHTS_DIR") or ""
endpoint = os.environ.get("HAKOCHUU_VC_SERVER_URL") or ""
repo = os.environ.get("HAKOCHUU_VC_REPO_DIR") or ""
try:
    import yaml
    manifest = yaml.safe_load((root / "shared/models_manifest.yaml").read_text(encoding="utf-8")) or {}
    vc = (manifest.get("models") or {}).get("vc") or {}
    if not engine:
        engine = str(vc.get("engine") or "seed_vc")
    if not weights:
        weights = str(vc.get("weights_dir") or "")
    if not endpoint:
        endpoint = str(vc.get("endpoint") or "http://127.0.0.1:23335")
except Exception:
    engine = engine or "seed_vc"
    weights = weights or "/path/to/pretrain_models/seed-vc"
    endpoint = endpoint or "http://127.0.0.1:23335"

if not repo:
    candidate = Path("/path/to/projects/seed-vc")
    repo = str(candidate) if candidate.is_dir() else ""

match = re.search(r"https?://([^:/]+):(\d+)", endpoint)
host = match.group(1) if match else "127.0.0.1"
port = match.group(2) if match else "23335"
if host in {"localhost", "0.0.0.0"}:
    host = "127.0.0.1"
print(engine)
print(weights)
print(host)
print(port)
print(repo)
PY
}

wait_http() {
  local url="$1"
  local name="$2"
  local timeout="${3:-180}"
  local pid="${4:-}"
  local i=0
  echo "等待 ${name} 就绪 (${url})..."
  while (( i < timeout )); do
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      echo "错误: ${name} 进程已退出，请查看日志。" >&2
      return 1
    fi
    if curl -sf --max-time 2 "$url" >/dev/null 2>&1; then
      echo "${name} 已就绪"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "错误: ${name} 启动超时 ${timeout}s" >&2
  return 1
}

start_vllm() {
  if ! should_run_vllm; then
    echo "已跳过 vLLM（HAKOCHUU_LLM_MOCK 或 HAKOCHUU_START_VLLM=false）。"
    return 0
  fi
  if [[ ! -x "$VLLM_BIN" ]]; then
    echo "错误: 未找到 $VLLM_BIN，请先安装 backend[dev,llm]。" >&2
    exit 1
  fi

  local weights served vllm_host vllm_port max_len model_id
  {
    read -r weights
    read -r served
    read -r vllm_host
    read -r vllm_port
    read -r max_len
    read -r model_id
  } < <(load_vllm_config)

  if [[ ! -d "$weights" ]]; then
    echo "错误: 模型权重目录不存在: $weights" >&2
    exit 1
  fi

  if port_in_use "$vllm_port"; then
    echo "vLLM 已在 ${vllm_host}:${vllm_port} 运行，复用现有进程。"
    return 0
  fi

  local gpu_mem="${HAKOCHUU_VLLM_GPU_MEM:-0.2}"
  echo "正在启动 vLLM: ${model_id} -> ${vllm_host}:${vllm_port}"
  echo "  weights=${weights}"
  echo "  served_model_name=${served}  max_model_len=${max_len}"
  echo "  日志: ${VLLM_LOG}"

  setsid "$VLLM_BIN" serve "$weights" \
    --host "$vllm_host" \
    --port "$vllm_port" \
    --served-model-name "$served" \
    --dtype auto \
    --max-model-len "$max_len" \
    --gpu-memory-utilization "$gpu_mem" \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    >"$VLLM_LOG" 2>&1 < /dev/null &
  local vllm_pid=$!
  echo "$vllm_pid" >"$VLLM_PID_FILE"

  if ! wait_http "http://${vllm_host}:${vllm_port}/v1/models" "vLLM" 180 "$vllm_pid"; then
    echo "----- vLLM 日志尾部 -----" >&2
    tail -n 40 "$VLLM_LOG" >&2 || true
    stop_vllm
    exit 1
  fi
}

start_asr() {
  if ! should_run_asr; then
    echo "已跳过 ASR（HAKOCHUU_ASR_MOCK 或 HAKOCHUU_START_ASR=false）。"
    return 0
  fi

  local engine weights asr_host asr_port
  {
    read -r engine
    read -r weights
    read -r asr_host
    read -r asr_port
  } < <(load_asr_config)

  export HAKOCHUU_ASR_ENGINE="${HAKOCHUU_ASR_ENGINE:-$engine}"
  export HAKOCHUU_ASR_WEIGHTS_DIR="${HAKOCHUU_ASR_WEIGHTS_DIR:-$weights}"

  if [[ -n "$weights" && ! -d "$weights" ]]; then
    echo "错误: ASR 权重目录不存在: $weights" >&2
    exit 1
  fi

  if port_in_use "$asr_port"; then
    echo "ASR 已在 ${asr_host}:${asr_port} 运行，复用现有进程。"
    return 0
  fi

  echo "正在启动 ASR (${engine}) -> ${asr_host}:${asr_port}"
  echo "  weights=${HAKOCHUU_ASR_WEIGHTS_DIR}"
  echo "  日志: ${ASR_LOG}"

  local old_pwd
  old_pwd="$(pwd)"
  cd "${REPO_ROOT}/backend"
  setsid "$UVICORN_BIN" app.services.asr_server:app --host "$asr_host" --port "$asr_port" \
    >"$ASR_LOG" 2>&1 < /dev/null &
  local asr_pid=$!
  cd "$old_pwd"
  echo "$asr_pid" >"$ASR_PID_FILE"

  if ! wait_http "http://${asr_host}:${asr_port}/health" "ASR" 180 "$asr_pid"; then
    echo "----- ASR 日志尾部 -----" >&2
    tail -n 40 "$ASR_LOG" >&2 || true
    stop_asr
    exit 1
  fi
}

start_tts() {
  if ! should_run_tts; then
    echo "已跳过 TTS（HAKOCHUU_TTS_MOCK 或 HAKOCHUU_START_TTS=false）。"
    return 0
  fi

  local engine weights tts_host tts_port repo
  {
    read -r engine
    read -r weights
    read -r tts_host
    read -r tts_port
    read -r repo
  } < <(load_tts_config)

  export HAKOCHUU_TTS_ENGINE="${HAKOCHUU_TTS_ENGINE:-$engine}"
  export HAKOCHUU_TTS_WEIGHTS_DIR="${HAKOCHUU_TTS_WEIGHTS_DIR:-$weights}"
  export HAKOCHUU_COSYVOICE_REPO_DIR="${HAKOCHUU_COSYVOICE_REPO_DIR:-$repo}"
  if [[ -z "${HAKOCHUU_TTS_DEFAULT_REF_WAV:-}" && -n "$repo" && -f "${repo}/asset/zero_shot_prompt.wav" ]]; then
    export HAKOCHUU_TTS_DEFAULT_REF_WAV="${repo}/asset/zero_shot_prompt.wav"
  fi

  if [[ -n "$weights" && ! -d "$weights" ]]; then
    echo "错误: TTS 权重目录不存在: $weights" >&2
    exit 1
  fi
  if [[ "$engine" == "cosyvoice3" && -n "$repo" && ! -d "$repo" ]]; then
    echo "错误: CosyVoice 源码目录不存在: $repo" >&2
    exit 1
  fi

  if port_in_use "$tts_port"; then
    echo "TTS 已在 ${tts_host}:${tts_port} 运行，复用现有进程。"
    return 0
  fi

  echo "正在启动 TTS (${engine}) -> ${tts_host}:${tts_port}"
  echo "  weights=${HAKOCHUU_TTS_WEIGHTS_DIR}"
  echo "  repo=${HAKOCHUU_COSYVOICE_REPO_DIR}"
  echo "  日志: ${TTS_LOG}"

  local old_pwd
  old_pwd="$(pwd)"
  cd "${REPO_ROOT}/backend"
  setsid "$UVICORN_BIN" app.services.tts_server:app --host "$tts_host" --port "$tts_port" \
    >"$TTS_LOG" 2>&1 < /dev/null &
  local tts_pid=$!
  cd "$old_pwd"
  echo "$tts_pid" >"$TTS_PID_FILE"

  if ! wait_http "http://${tts_host}:${tts_port}/health" "TTS" 300 "$tts_pid"; then
    echo "----- TTS 日志尾部 -----" >&2
    tail -n 40 "$TTS_LOG" >&2 || true
    stop_tts
    exit 1
  fi
}

start_vc() {
  if ! should_run_vc; then
    echo "已跳过 VC（HAKOCHUU_VC_MOCK 或 HAKOCHUU_START_VC=false）。"
    return 0
  fi

  local engine weights vc_host vc_port repo
  {
    read -r engine
    read -r weights
    read -r vc_host
    read -r vc_port
    read -r repo
  } < <(load_vc_config)

  export HAKOCHUU_VC_ENGINE="${HAKOCHUU_VC_ENGINE:-$engine}"
  export HAKOCHUU_VC_WEIGHTS_DIR="${HAKOCHUU_VC_WEIGHTS_DIR:-$weights}"
  export HAKOCHUU_VC_REPO_DIR="${HAKOCHUU_VC_REPO_DIR:-$repo}"
  if [[ -z "${HAKOCHUU_VC_DEFAULT_REF_WAV:-}" && -n "${HAKOCHUU_COSYVOICE_REPO_DIR:-}" \
      && -f "${HAKOCHUU_COSYVOICE_REPO_DIR}/asset/zero_shot_prompt.wav" ]]; then
    export HAKOCHUU_VC_DEFAULT_REF_WAV="${HAKOCHUU_COSYVOICE_REPO_DIR}/asset/zero_shot_prompt.wav"
  fi

  if [[ -n "$weights" ]]; then
    mkdir -p "$weights"
  fi
  if [[ -z "$repo" || ! -d "$repo" ]]; then
    echo "错误: Seed-VC 源码目录不存在: ${repo:-'(未配置 HAKOCHUU_VC_REPO_DIR)'}" >&2
    exit 1
  fi

  if port_in_use "$vc_port"; then
    echo "VC 已在 ${vc_host}:${vc_port} 运行，复用现有进程。"
    return 0
  fi

  echo "正在启动 VC (${engine}) -> ${vc_host}:${vc_port}"
  echo "  weights=${HAKOCHUU_VC_WEIGHTS_DIR}"
  echo "  repo=${HAKOCHUU_VC_REPO_DIR}"
  echo "  日志: ${VC_LOG}"

  local old_pwd
  old_pwd="$(pwd)"
  cd "${REPO_ROOT}/backend"
  setsid "$UVICORN_BIN" app.services.vc_server:app --host "$vc_host" --port "$vc_port" \
    >"$VC_LOG" 2>&1 < /dev/null &
  local vc_pid=$!
  cd "$old_pwd"
  echo "$vc_pid" >"$VC_PID_FILE"

  if ! wait_http "http://${vc_host}:${vc_port}/health" "VC" 420 "$vc_pid"; then
    echo "----- VC 日志尾部 -----" >&2
    tail -n 40 "$VC_LOG" >&2 || true
    stop_vc
    exit 1
  fi
}

start_uvicorn() {
  local background="$1"
  cd "${REPO_ROOT}/backend"
  if [[ "$background" == "bg" ]]; then
    nohup "$UVICORN_BIN" app.main:app --host "$HOST" --port "$PORT" >"$UVICORN_LOG" 2>&1 < /dev/null &
    echo $! >"$UVICORN_PID_FILE"
    echo "服务端 FastAPI 已在后台启动 (PID: $(cat "$UVICORN_PID_FILE"), 监听: ${HOST}:${PORT})"
    echo "FastAPI 日志: tail -f ${UVICORN_LOG}"
    if should_run_vllm; then
      echo "vLLM 日志:     tail -f ${VLLM_LOG}"
    fi
    if should_run_asr; then
      echo "ASR 日志:      tail -f ${ASR_LOG}"
    fi
    if should_run_tts; then
      echo "TTS 日志:      tail -f ${TTS_LOG}"
    fi
    if should_run_vc; then
      echo "VC 日志:       tail -f ${VC_LOG}"
    fi
    echo "停止全部服务: ./scripts/start_server.sh --stop"
  else
    echo "正在前台启动服务端 FastAPI (${HOST}:${PORT})，按 Ctrl+C 同时停止 vLLM/ASR/TTS/VC..."
    echo "FastAPI 日志同步写入: ${UVICORN_LOG}"
    : >"$UVICORN_LOG"
    "$UVICORN_BIN" app.main:app --host "$HOST" --port "$PORT" --reload > >(tee -a "$UVICORN_LOG") 2>&1 &
    echo $! >"$UVICORN_PID_FILE"
    wait "$(cat "$UVICORN_PID_FILE")"
  fi
}

CLEANING=0
cleanup() {
  if [[ "$CLEANING" == "1" ]]; then
    return 0
  fi
  CLEANING=1
  echo ""
  echo "正在停止 Hakochuu 服务（FastAPI + vLLM + ASR + TTS + VC）..."
  stop_uvicorn
  stop_vc
  stop_tts
  stop_asr
  stop_vllm
  echo "已停止。"
}

case "${1:-}" in
  --stop)
    cleanup
    exit 0
    ;;
  --bg)
    start_vllm
    start_asr
    start_tts
    start_vc
    start_uvicorn bg
    ;;
  "" )
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    start_vllm
    start_asr
    start_tts
    start_vc
    start_uvicorn fg
    ;;
  *)
    echo "用法: $0 [--bg|--stop]" >&2
    exit 1
    ;;
esac
