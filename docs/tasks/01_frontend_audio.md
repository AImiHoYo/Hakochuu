# 任务卡 01：前端音频引擎与交互工作台 (`frontend`)

- **状态**：待认领 / 就绪
- **前置阅读**：`AGENTS.md`、`docs/project_structure_spec.md`、`shared/protocol/frame_spec.json`、`shared/llm_models/qwen2.5-social-3b-nb-chat/model.yaml`

---

## 1. 允许修改目录 (Write Scope)

- `frontend/src/audio/**`（VAD 打断、16-Byte 帧编解码、AudioWorklet、WebSocket/WebRTC 客户端、双轨混音器、引导录音）
- `frontend/src/avatar/**`（`0x04` 口型同步对齐与渲染器接口）
- `frontend/src/components/**`（对话字幕、翻唱面板、角色引导与微调进度、模型参数面板、`DEBUG_WARNING` 提示框）
- `frontend/src/pages/**`（`WorkspacePage.tsx`、`OnboardingPage.tsx`、`SettingsPage.tsx`）
- `frontend/src/services/**`、`frontend/src/stores/**`、`frontend/src/types/**`
- `frontend/public/**`（WASM / Worklet 静态脚本）

> **禁止修改**：`backend/**`、`scripts/**`、`shared/protocol/frame_spec.json`、`shared/models_manifest.yaml`。若需新增 REST 字段或 WS 信令，须先在 `docs/decisions.md` 登记并与后端窗口对齐。

---

## 2. 核心职责与待推进事项

1. **实时音频与本地打断**：维护 `AudioWorklet` 20ms 采集/流式播放、Silero VAD 本地 `<10ms` Barge-in 检测与 `gen_id` 自增丢帧逻辑。
2. **双轨混音与口型预留**：保障 `0x03` 纯人声干声流与 `206 Range` 伴奏流按 `PTS` 精确对齐，保持 `0x04` `lipSync.ts` 消费链路畅通。
3. **动态模型参数与降级提示**：根据后端下发的 `shared/llm_models/<model_id>/model.yaml` 动态渲染专属参数面板（如 `social_scene`、`current_topic`、`temperature`、`max_tokens`）；收到 `DEBUG_WARNING` 时在右上角展示非阻塞警告。
4. **联调体验优化**：配合隧道 (`http://localhost:5173` -> `23333`) 验证全双工对话、点歌翻唱、角色秒传建档与 SSE 微调监控的端到端交互。

---

## 3. 验收标准

```bash
cd /path/to/Hakochuu/frontend
npx tsc --noEmit
npm run build
```
- 无 TypeScript 类型错误，Vite 生产构建通过，且未改动 `frontend/` 以外的文件。
