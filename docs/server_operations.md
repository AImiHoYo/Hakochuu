# Hakochuu GPU 服务端部署与操作手册

本文档面向 **本地 Mac 开发机 + 远端 Linux GPU 服务器** 协同开发场景，覆盖代码同步、虚拟环境构建、环境变量配置、服务启停、SSH 隧道联调与端口管理。

---

## 1. 服务与端口规划

| 端口 | 服务模块 | 说明 |
| :--- | :--- | :--- |
| `23333` | FastAPI 网关 (`uvicorn`) | 提供 `/v1/*` REST 接口与 `/v1/session` WebSocket 双工通道 |
| `23334` | 流式 TTS 服务 | CosyVoice 3 / Qwen3-TTS 推理服务 |
| `23335` | 零样本 VC 服务 | Seed-VC 翻唱干声转换服务 |
| `23336` | 语音识别 ASR 服务 | SenseVoice / Faster-Whisper 实时转写服务 |
| `11434` | 本地 LLM (Ollama / vLLM) | OpenAI 兼容流式对话与 `trigger_singing` 工具调用 |
| `6379` | Redis（可选） | 后期启用 RQ 异步微调与曲库分离队列时使用 |

---

## 2. 代码同步与远端虚拟环境构建（本地 Mac 执行）

在本地项目根目录执行交互式同步脚本，脚本会自动读取 `~/.ssh/config` 与历史连接记录：

```bash
cd /path/to/Hakochuu
./scripts/sync_server.sh
```

按提示完成以下操作：
1. 选择目标服务器（如 `<your-gpu-server>`）。
2. 输入或确认服务端项目目录（例如 `/path/to/projects/Hakochuu-server`，不存在时可自动 `mkdir -p` 创建）。
3. 选择操作：
   * `[1]` 仅同步本地代码到服务端（自动排除 `.venv/`、`frontend/`、`storage/`、`cache/`、`.env` 与 `*.db`）。
   * `[2]` 仅在服务端项目目录构建 `.venv` 并安装 `backend[dev]`。
   * `[3]` **同步代码 + 构建/更新服务端 `.venv`（推荐首次或更新依赖时使用）**。

### 2.1 临时新增/修改文件后的快速增量补充上传

本地新增或修改脚本、文档、代码后，**无需重复走交互菜单**，直接执行以下命令即可自动对比差异并只上传新增/变更的文件到最近一次连接的服务端目录：

```bash
./scripts/sync_server.sh --last
```

*(如果本次新增了 Python 第三方库导致 `backend/pyproject.toml` 变化，请改用 `.venv/bin/python scripts/server_cli.py --last --action all` 同步并自动更新远端 `.venv`。)*

---

## 3. 模型目录清单、缓存与服务端配置（GPU 服务器执行）

* **模型目录与缓存清单**：[shared/models_manifest.yaml](../shared/models_manifest.yaml) 集中定义了 `cache/`（`hf`、`torch`、`audio_separator`、`tmp`）及远端 LLM、TTS、Seed-VC、ASR、BS-RoFormer 模型权重的存放目录。
* **缓存目录**：项目根目录 `cache/` 已纳入 `.gitignore`，用于存放模型下载缓存与推理临时文件，不会随代码同步覆盖。

### 3.1 配置 `.env` 环境变量

首次部署时复制模板生成 `.env`：

```bash
ssh <你的SSH_Host别名，如 <your-gpu-server>>
cd /path/to/projects/Hakochuu-server
cp .env.example .env
```

根据当前联调阶段编辑 `.env`：

* **阶段 A：先验证网络与网关通路（Mock 模式，不依赖 GPU 模型）**
  ```env
  HAKOCHUU_DEPLOY_MODE=lan
  HAKOCHUU_HOST=localhost
  HAKOCHUU_PORT=23333
  HAKOCHUU_MOCK_ENGINES=true
  ```
* **阶段 B：对接服务端真实 Ollama / TTS / Seed-VC / ASR 模型**
  ```env
  HAKOCHUU_DEPLOY_MODE=lan
  HAKOCHUU_HOST=localhost
  HAKOCHUU_PORT=23333
  HAKOCHUU_MOCK_ENGINES=false
  HAKOCHUU_LLM_BASE_URL=http://localhost:11434/v1
  HAKOCHUU_LLM_MODEL=qwen2.5:14b-instruct-q4_K_M
  HAKOCHUU_TTS_SERVER_URL=http://localhost:23334
  HAKOCHUU_VC_SERVER_URL=http://localhost:23335
  HAKOCHUU_ASR_SERVER_URL=http://localhost:23336
  HAKOCHUU_VRAM_LIMIT_MB=24576
  ```

### 3.2 运行服务端自检测试

```bash
cd /path/to/projects/Hakochuu-server/backend
../.venv/bin/pytest -v
```

---

## 4. 启动与停止服务端（FastAPI + vLLM + ASR + TTS）

`./scripts/start_server.sh` 会按配置自动拉起 vLLM（`11434`）、真实 ASR（`23336`，SenseVoice/funasr）、真实 TTS（`23334`，CosyVoice3）、真实 Seed-VC（`23335`）和 FastAPI 网关（`23333`）。前台 `Ctrl+C` 或 `--stop` 会同时停掉它们。

- 跳过 vLLM：`HAKOCHUU_LLM_MOCK=true` 或 `HAKOCHUU_START_VLLM=false`
- 跳过 ASR：`HAKOCHUU_ASR_MOCK=true` 或 `HAKOCHUU_START_ASR=false`
- 跳过 TTS：`HAKOCHUU_TTS_MOCK=true` 或 `HAKOCHUU_START_TTS=false`
- 跳过 VC：`HAKOCHUU_VC_MOCK=true` 或 `HAKOCHUU_START_VC=false`

### 4.1 前台启动（推荐开发调试使用，支持代码热重载）

```bash
cd /path/to/projects/Hakochuu-server
./scripts/start_server.sh
```

### 4.2 后台常驻启动（SSH 断开后保持运行）

```bash
cd /path/to/projects/Hakochuu-server
./scripts/start_server.sh --bg
```

查看实时日志：

```bash
tail -f /path/to/projects/Hakochuu-server/storage/uvicorn.log
tail -f /path/to/projects/Hakochuu-server/storage/vllm.log
tail -f /path/to/projects/Hakochuu-server/storage/asr.log
tail -f /path/to/projects/Hakochuu-server/storage/tts.log
tail -f /path/to/projects/Hakochuu-server/storage/vc.log
```

停止 FastAPI、vLLM、ASR、TTS 与 VC：

```bash
./scripts/start_server.sh --stop
```

### 4.3 端口查看与优雅关闭（本地 Mac 一键操作）

在本地 Mac 运行端口管理脚本，无需手动登录远端查 PID：

```bash
cd /path/to/Hakochuu
./scripts/port_ctl.sh
```

* 选择 `[1]` 查看远端核心端口（`23333/11434/23334/23335/23336`）占用情况，输入端口号即可优雅关闭。
* 选择 `[3]` 可同时对比本地 Mac 与远端服务器的端口状态。

---

## 5. 本地 Mac 建立隧道与前端联调

1. **本地终端 1：建立 SSH 端口转发隧道**
   ```bash
   cd /path/to/Hakochuu
   ./scripts/tunnel_server.sh
   ```
   保持此终端开启，远端的 `23333 / 11434 / 23334 / 23335 / 23336` 端口将安全映射到本地。

2. **本地终端 2：启动 Vite 前端**
   ```bash
   cd /path/to/Hakochuu/frontend
   npm run dev
   ```
   打开浏览器访问 `http://localhost:5173`：
   * 顶部确认后端状态显示 `已连接`。
   * 点击 **连接 /v1/session** 后可测试麦克风全双工、文本对话 (`0x02`)、点歌 (`0x03`) 与角色引导建档。

---

## 6. 音频素材命令行上传（开发调试用）

如需直接从本地向服务端上传测试干声或曲库文件（自动走 SHA-256 秒传校验）：

```bash
cd /path/to/Hakochuu
.venv/bin/python scripts/upload_to_server.py \
  --server http://localhost:23333 \
  --file ./demo_ref.wav \
  --kind speech \
  --character-id char_001
```

---

## 7. 常见问题排查

| 现象 | 排查要点 |
| :--- | :--- |
| 前端提示 `/v1/system/capabilities` 不可达 | 确认远端 `uvicorn` 已启动在 `23333` 端口，且本地 `tunnel_server.sh` 隧道终端处于运行状态 |
| 隧道启动报 `Address already in use` | 本地对应端口被残留进程占用，运行 `./scripts/port_ctl.sh` 选择 `[2]` 检查并关闭 |
| 对话返回仍是模拟音频 | 检查服务端是否执行了 `set -a && source .env && set +a`，确认 `.env` 中 `HAKOCHUU_MOCK_ENGINES=false` |
| LLM 报错 `404 model not found` | 在服务端执行 `ollama list`（或检查 vLLM 启动参数），核对 `.env` 中的 `HAKOCHUU_LLM_MODEL` |
