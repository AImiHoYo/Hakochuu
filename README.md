# Hakochuu

面向个人开发机与局域网 GPU 服务器的低延迟 AI 语音对话、零样本 AI 翻唱及虚拟形象 WebUI。

- 架构方案：[docs/voice_webui_full_architecture.md](docs/voice_webui_full_architecture.md)
- 目录与代码规范：[docs/project_structure_spec.md](docs/project_structure_spec.md)
- 服务端部署与操作手册：[docs/server_operations.md](docs/server_operations.md)

## 技术栈

- **前端**：Vite + React + TypeScript（边缘 WASM Silero VAD、Web Audio 双轨混音、16-Byte 二进制协议）
- **后端**：FastAPI + SQLite (`aiosqlite`) + Redis/RQ（Python 依赖由 `uv` 管理，虚拟环境位于根目录 `.venv/`）
- **模型推理**：支持对接局域网 GPU 服务器（vLLM/Ollama、CosyVoice 3/Qwen3-TTS、Seed-VC、BS-RoFormer），本地无 GPU 时可开启 `HAKOCHUU_MOCK_ENGINES=true` 联调。

## 快速开始

### 1. 初始化后端环境（根目录 `.venv`）

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e "backend[dev]"
```

运行基础测试：

```bash
cd backend && ../.venv/bin/pytest
```

### 2. 启动后端网关

```bash
cp .env.example .env
cd backend && ../.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 23333
```

### 3. 启动前端开发服务器

```bash
cd frontend
npm install
npm run dev
```
