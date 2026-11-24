# 任务卡 03：服务端 GPU 模型推理接入与运维协同 (`engines` & `scripts`)

- **状态**：待认领 / 就绪
- **前置阅读**：`AGENTS.md`、`docs/server_operations.md`、`shared/models_manifest.yaml`、`shared/llm_models/qwen2.5-social-3b-nb-chat/model.yaml`

---

## 1. 允许修改目录 (Write Scope)

- `backend/app/engines/**`（`base.py`、`asr_engine.py`、`llm_router.py`、`tts_engine.py`、`vc_engine.py`、`separator.py`、`lip_sync.py`）
- `backend/app/workers/**`（`rq_app.py`、`finetune_worker.py`、`separator_worker.py`）
- `shared/llm_models/**`、`shared/models_manifest.yaml`
- `scripts/**`、`.env.example`

> **禁止修改**：`frontend/**`、`backend/app/ws/protocol.py`、`backend/app/api/**`。所有引擎必须通过 `backend/app/core/gpu_manager.py` 申请显存，严禁私自 `.to("cuda")`。

---

## 2. 核心职责与待推进事项

1. **真实模型服务对接 (`<your-gpu-server>`)**：
   - **LLM (`11434`)**：对接 vLLM / Ollama，优先打通 `Qwen2.5-Social-3B-NB-Chat` 流式对话与 `[emotion]` / `trigger_singing` 解析。
   - **TTS (`23334`)**：对接 CosyVoice 3 / Qwen3-TTS 流式合成与角色 LoRA 热挂载。
   - **VC (`23335`)**：对接 Seed-VC 零样本翻唱转换，确保仅输出纯人声干声流 (`0x03`) 与毫秒级 `PTS`。
   - **ASR (`23336`)**：对接 SenseVoice / Faster-Whisper 实时识别与引导录音标注。
2. **离线分离与微调**：完善 `BS-RoFormer` 伴奏分离与异步微调 worker，所有缓存严格落盘至根目录 `cache/`。
3. **无真实模型时的降级配合**：引擎层检测到远端推理服务未启动或权重缺失时，向上层抛出可识别的降级信号以触发 `DEBUG_WARNING`，不阻塞文本对话。
4. **同步与联调**：修改后使用 `./scripts/sync_server.sh --last` 增量同步至 `<your-gpu-server>`，配合 `tunnel_server.sh` 与 `port_ctl.sh` 验证各端口健康状态。

---

## 3. 验收标准

```bash
cd /path/to/Hakochuu/backend
../.venv/bin/pytest -q
```
- 引擎层 Mock/真实模式切换自检通过，后端 `pytest` 全绿；同步脚本执行无报错且远端端口 (`23333` / `11434` / `23334` / `23335` / `23336`) 连通性正常。
