# Hakochuu 多窗口协同开发与防污染规范 (`AGENTS.md`)

> 本文件为所有接入 **Hakochuu** 项目的开发窗口与 AI 助手的顶层工作契约。任何窗口开始编码前，必须先阅读本文件与所属任务卡。

---

## 1. 只读核心契约（严禁随意推翻）

在修改代码前，请先确认以下契约文档与共享定义：
- `docs/project_structure_spec.md` — 仓库目录结构、模块边界与命名规范
- `docs/server_operations.md` — `<your-gpu-server>` 服务端部署、SSH 隧道与协同脚本手册
- `shared/protocol/frame_spec.json` — 16-Byte 大端序二进制帧头 (`>BBHIII`)、Codec 与信令枚举
- `shared/models_manifest.yaml` — 模型权重根目录、端口与 `cache/` 路径规划
- `shared/llm_models/<model_id>/model.yaml` — 模型专属参数与社交场景注入规范（如 `qwen2.5-social-3b-nb-chat/model.yaml`）

*(注：若后续补充 `docs/voice_webui_full_architecture.md`，亦纳入只读核心契约。)*

---

## 2. 已冻结的技术栈与端口规范

| 服务 / 组件 | 规范 |
| :--- | :--- |
| **前端** | `Vite + React + TypeScript` (`frontend/`) |
| **后端网关** | `FastAPI + SQLite (aiosqlite)` (`backend/`)，Python 依赖由 `uv` 管理并安装于根目录 `.venv/` |
| **缓存目录** | 项目根目录 `cache/`（`hf` / `torch` / `audio_separator` / `tmp`），已加入 `.gitignore` |
| **固定端口** | `23333` FastAPI 网关 · `11434` LLM (vLLM/Ollama) · `23334` TTS · `23335` Seed-VC · `23336` ASR |

---

## 3. 架构红线（所有窗口必须遵守）

1. **禁止 Base64 传输实时音频**：所有实时语音上行 (`0x01`)、TTS (`0x02`)、VC 纯人声干声 (`0x03`) 与口型同步 (`0x04`) 必须走 `16-Byte` Big-Endian 二进制帧。
2. **打断防残留 (`gen_id`)**：VAD 本地打断自增 `gen_id` 并发送 `INTERRUPT`；前后端均须丢弃 `frame.gen_id < current_gen_id` 的滞留帧。
3. **时间戳对齐 (`PTS`)**：`0x03` 纯人声干声帧与 `0x04` 口型同步帧必须携带一致的毫秒级 `PTS`。
4. **LLM 与角色库解耦**：所有 LLM 共享 `character_profiles` 角色库（音色、LoRA、形象）；切换 LLM 时动态读取 `shared/llm_models/<model_id>/model.yaml` 并叠加专属参数。
5. **模型缺失降级策略**：缺少 TTS 或 VC 模型时，**必须保留文本回复**并触发 `DEBUG_WARNING`（前端右上角提示框 + 后端 `[Debug]` 日志），不得静默吞错或中断对话链路。
6. **单卡显存互斥**：`tts_engine.py` 与 `vc_engine.py` 严禁绕过 `backend/app/core/gpu_manager.py` 直接 `.to("cuda")`。
7. **资产与路径安全**：`local_path` 直传仅限本地模式且须校验白名单；`storage/`、`cache/`、`.venv/`、`.env`、`*.db` 严禁提交 Git。

---

## 4. 多窗口防污染隔离规则

1. **单窗口单任务卡**：每个窗口只认领 `docs/tasks/` 下的一张任务卡，并严格限制在任务卡规定的 **允许修改目录 (Write Scope)** 内改动文件。
2. **禁止跨区静默修改**：
   - 前端窗口不得修改 `backend/` 或 `scripts/`。
   - 后端网关窗口不得修改 `frontend/`。
   - GPU 推理窗口不得擅自改动 `frontend/` 或破坏 `backend/app/ws/protocol.py` 既有帧格式。
3. **跨模块契约变更必须登记**：如确需修改 `shared/`、数据库 Schema (`backend/app/db/models.py`)、REST 路由签名或端口约定，必须先在 `docs/decisions.md` 追加一条变更记录，说明影响范围与兼容策略。
4. **服务端同步规范**：向 `<your-gpu-server>` 同步代码统一使用 `./scripts/sync_server.sh --last`（默认排除 `frontend/`、`.venv/`、`storage/`、`cache/`、`.env` 与 `*.db`）。

---

## 5. 交接前基线自检清单

任何窗口完成开发或交接前，必须运行对应检查并汇报结果：
- **后端检查**：`cd /path/to/Hakochuu/backend && ../.venv/bin/pytest -q`
- **前端检查**：`cd /path/to/Hakochuu/frontend && npx tsc --noEmit`（涉及页面/构建改动时补充 `npm run build`）
