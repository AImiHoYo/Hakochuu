# Hakochuu 架构决策与跨窗口变更日志 (`decisions.md`)

> 本文件用于记录跨窗口、跨模块（`shared/`、端口、数据库 Schema、API 契约、依赖变更）的决策与变更历史。
> **规则**：任何窗口如需修改所属任务卡边界之外的共享契约，必须在此文件末尾追加新条目（按时间倒序或正序追加，严禁删除历史记录）。

---

## [2026-11-24] <your-gpu-server> 启用真实 CosyVoice3 TTS（23334）

- **变更**：`shared/models_manifest.yaml` 的 TTS 权重指向 `/path/to/pretrain_models/Fun-CosyVoice3-0.5B`；`backend/pyproject.toml` 增加 `tts` extra（HyperPyYAML / wetext / onnxruntime / openai-whisper，不钉死 torch）；`scripts/start_server.sh` 增加 `start_tts` / `stop_tts`。
- **运行时**：CosyVoice 源码仓库通过 `HAKOCHUU_COSYVOICE_REPO_DIR` 注入 `sys.path`，避免把 CosyVoice `requirements.txt` 的 `torch==2.3.1` 装进 `.venv`（会破坏 vLLM）。
- **兼容**：`HAKOCHUU_TTS_MOCK=true` 或 `HAKOCHUU_START_TTS=false` 时跳过 TTS sidecar，网关仍按既有 `DEBUG_WARNING` 降级为文本回复。

## [2026-11-24] Phase 1–5 基线冻结与多窗口隔离结构初始化

- **状态**：已确认基线通过（后端 `pytest` `18 passed`；前端 `tsc --noEmit` 通过）。
- **冻结约定**：
  1. **固定端口**：`23333` (FastAPI 网关)、`11434` (LLM: vLLM/Ollama)、`23334` (TTS)、`23335` (Seed-VC)、`23336` (ASR)。
  2. **16-Byte 二进制协议**：遵循 `shared/protocol/frame_spec.json` (`>BBHIII`)，支持 `0x01`~`0x04` 帧类型与 `gen_id` 打断过滤。
  3. **多模型共享角色库**：所有 LLM 共享 `character_profiles` 表；模型切换动态读取 `shared/llm_models/<model_id>/model.yaml`（当前默认 `qwen2.5-social-3b-nb-chat`）。
  4. **优雅降级 (`DEBUG_WARNING`)**：缺少 TTS/VC 模型时保留文本回复，并通过信令/状态触发前端右上角提示框与后端 `[Debug]` 日志。
  5. **多窗口任务拆分**：在 `docs/tasks/` 下拆分为 `01_frontend_audio.md`、`02_backend_gateway.md`、`03_server_gpu_inference.md` 三张独立任务卡，配合根目录 `AGENTS.md` 实现写入隔离。
- **说明**：当前 `docs/` 目录中暂未包含 `voice_webui_full_architecture.md`，现有架构与代码规范以 `docs/project_structure_spec.md` 与 `docs/server_operations.md` 为准。

---

## [2026-11-24] 语音链路第 1–3 步落地与模型目录统一

- **状态**：`11434` (vLLM `Qwen2.5-Social-3B-NB-Chat`)、`23336` (ASR `SenseVoiceSmall`)、`23334` (TTS `Fun-CosyVoice3-0.5B`) 与 `23333` (FastAPI 网关) 已全部打通并合入 `scripts/start_server.sh`。
- **共享契约更新**：
  1. **引擎独立开关**：新增 `HAKOCHUU_ASR_MOCK`、`HAKOCHUU_TTS_MOCK`、`HAKOCHUU_VC_MOCK`（未设置时继承 `HAKOCHUU_MOCK_ENGINES`），`/v1/system/capabilities` 同步暴露 `engines` 状态。
  2. **双工回环控制**：`HELLO` 新增可选 `echo_loopback` 字段（前端默认 `false`），避免全双工语音输入将自身音频回环为 `0x02`。
  3. **统一模型目录**：`shared/models_manifest.yaml` 中的 `paths.models_root` 及 `vc`、`separator` 权重路径统一修正为 `/path/to/pretrain_models/`。

## [2026-11-24] <your-gpu-server> 启用真实 Seed-VC（23335）与 BS-RoFormer 分离依赖

- **变更**：`backend/pyproject.toml` 增加 `vc` extra（`munch` / `pydub`，不钉死 torch）与 `separator` extra（`audio-separator` / `audioread`）；`scripts/start_server.sh` 增加 `start_vc` / `stop_vc`，监听 `127.0.0.1:23335`。
- **运行时**：Seed-VC 源码仓库通过 `HAKOCHUU_VC_REPO_DIR` 注入 `sys.path`（默认 `/path/to/projects/seed-vc`），权重缓存落到 `/path/to/pretrain_models/seed-vc`；`vc_server.py` 对接官方 `SeedVCWrapper.convert_voice`，仅输出 24kHz PCM16 干声（960B / 20ms）。
- **兼容**：`HAKOCHUU_VC_MOCK=true` 或 `HAKOCHUU_START_VC=false` 时跳过 VC sidecar，网关仍按既有 `DEBUG_WARNING` 降级，不阻断文本对话。

## [2026-11-24] 全链路服务端结构化日志与 `/v1/logs/*` 监控接口

- **日志落盘与格式**：
  - `scripts/start_server.sh` 前台启动 FastAPI 网关时通过 `tee -a` 同步写入 `storage/uvicorn.log`，与 `--bg` 模式保持一致。
  - 新增 `backend/app/core/logging.py`，统一输出格式 `%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s`，覆盖网关、ASR、TTS、VC 微服务以及 `pipeline.py` / `gpu_manager.py` / `session.py` 的延迟指标、显存切换、`DEBUG_WARNING` 与异常堆栈。
- **REST & SSE 契约（`backend/app/api/v1/logs.py`）**：
  - 严格使用服务白名单映射（`gateway`/`uvicorn` -> `uvicorn.log`、`vllm` -> `vllm.log`、`asr` -> `asr.log`、`tts` -> `tts.log`、`vc` -> `vc.log`），未命中白名单返回 `404`，严禁任意文件路径参数。
  - `GET /v1/logs/services`：返回各服务的可用性、文件大小、更新时间及端口健康状态。
  - `GET /v1/logs/{service}?lines=200&level=&q=`：支持尾部 N 行读取、日志级别（`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`）与关键字过滤（非法级别返回 `400`）。
  - `GET /v1/logs/{service}/stream`：基于 SSE (`text/event-stream`) 推送初始尾部日志与增量行，支持 15s 心跳保活与客户端断开清理。
- **前端入口**：`frontend/src/App.tsx` 顶部导航新增 `/logs`（`LogsPage.tsx`），并在 `WorkspacePage.tsx` 调试区提供快捷跳转链接。

## [2026-11-24] 曲库分离异步队列（`/v1/songs/separate/jobs`）、自动识别歌手/歌名与顺序编号

- **异步接口（`backend/app/api/v1/songs.py`）**：
  - `GET /v1/songs/next-id`：扫描现有曲库与进行中的分离任务，返回下一个递增数字编号（`1, 2, 3...`）。
  - `POST /v1/songs/separate/jobs`（`202`）：上传原曲后立即返回 `job_id` 并在后台串行执行 BS-RoFormer 分离（单卡信号量防并发 OOM）；`song_id`/`title`/`artist` 均支持省略时由服务端自动分配序号与从文件名提取。
  - `GET /v1/songs/separate/jobs`、`GET /v1/songs/separate/jobs/{job_id}` 与 `GET /v1/songs/separate/jobs/{job_id}/stream`（SSE）：支持切页返回后恢复实时进度条，旧版同步 `POST /v1/songs/separate` 保持兼容。
- **前端体验（`SettingsPage.tsx`）**：选定音频文件后自动解析 `歌手 - 歌名` 并填充表单，`song_id` 默认按 `1, 2, 3...` 顺延，提交后即可自由切换「服务日志」等页面。
