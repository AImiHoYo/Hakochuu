# 任务卡 02：后端 FastAPI 网关、会话调度与持久化 (`backend` 核心层)

- **状态**：待认领 / 就绪
- **前置阅读**：`AGENTS.md`、`docs/project_structure_spec.md`、`shared/protocol/frame_spec.json`、`shared/models_manifest.yaml`

---

## 1. 允许修改目录 (Write Scope)

- `backend/app/main.py`、`backend/app/config.py`
- `backend/app/api/**`（REST v1 路由：`/system`、`/assets`、`/characters`、`/finetune`、`/songs`、模型清单与动态参数读取）
- `backend/app/ws/**`（`/v1/session` WebSocket/WebRTC 调度、16-Byte 帧编解码、`HELLO` 编码协商）
- `backend/app/core/**`（`gpu_manager.py`、`spk_cache.py`、`pipeline.py`）
- `backend/app/db/**`、`backend/app/schemas/**`
- `backend/tests/**`

> **禁止修改**：`frontend/**`、`scripts/**`、`shared/protocol/frame_spec.json`。若修改 `backend/app/engines/` 对外抽象接口或 `db/models.py` 字段，须先在 `docs/decisions.md` 登记。

---

## 2. 核心职责与待推进事项

1. **双工网关与协议边界**：维护 `23333` 端口 `/v1/session` 的 16-Byte 帧收发、`gen_id` 过期帧丢弃、`INTERRUPT` 快速止血与 `PTS` 透传。
2. **显存与角色资产调度**：通过 `GPUManager` 协调对话常驻与唱歌错峰换入换出；维护 `character_profiles` SQLite 持久化与 `SpkCache` 内存常驻。
3. **动态模型配置路由**：切换 LLM 时动态解析 `shared/llm_models/<model_id>/model.yaml`，将角色 System Prompt 与模型专属场景层（如 `social_scene`）安全拼接。
4. **优雅降级与调试观测**：当 TTS / VC 服务不可达或权重缺失时，保证 LLM 文本回复正常下发，并向前端推送 `DEBUG_WARNING` 同时输出后台 `[Debug]` 日志。

---

## 3. 验收标准

```bash
cd /path/to/Hakochuu/backend
../.venv/bin/pytest -q
```
- 所有单元测试与集成测试全绿，不破坏既有 16-Byte 协议与 REST 接口契约。
