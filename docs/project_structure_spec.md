# Hakochuu 文件夹与代码结构规范

> 本文档基于 [本地 AI 语音对话、翻唱及虚拟形象演进 WebUI 完整技术方案](./voice_webui_full_architecture.md) 制定，作为项目初始化与后续模块开发的目录结构、命名约定与模块边界规范。

---

## 1. 仓库顶层目录规划

```text
Hakochuu/
├── docs/                         # 架构设计、协议规范与开发文档
│   ├── voice_webui_full_architecture.md
│   └── project_structure_spec.md
├── shared/                       # 前后端共享的协议常量与 JSON Schema
│   └── protocol/
│       └── frame_spec.json       # 16-Byte 帧头枚举、Codec ID 与信令类型定义
├── frontend/                     # Vite + React + TypeScript 前端工程
├── backend/                      # FastAPI + Python 异步网关与推理调度服务
├── storage/                      # 本地运行时资产存储（已加入 .gitignore）
│   ├── characters/               # 角色档案资产（引导录音、Embedding、LoRA、形象）
│   ├── songs/                    # 离线分离曲库（lead_vocal.wav / instrumental.opus）
│   └── uploads/                  # 分片上传临时目录与 SHA-256 去重缓存
├── scripts/                      # 开发辅助、模型权重检查与曲库预处理脚本
├── .venv/                        # 项目 Python 虚拟环境（由 uv 管理，已忽略）
├── .gitignore                    # 忽略模型权重、运行时音频、虚拟环境与构建产物
└── README.md                     # 项目总览与本地/局域网启动指引
```

---

## 2. 前端目录结构与职责 (`frontend/`)

采用 **Feature + Core Layer** 分层，将边缘计算（WASM VAD、Opus、Web Audio 混音、二进制协议）与 UI 组件彻底解耦，便于后续无痛接入 Live2D / VRM 渲染。

```text
frontend/
├── index.html                    # Vite HTML 入口
├── vite.config.ts                # Vite 构建配置与 /v1、/v1/session 反向代理
├── public/
│   ├── wasm/                     # Silero VAD ONNX/WASM 模型与运行库
│   └── worklets/                 # 编译后的 AudioWorklet 处理器静态脚本
├── src/
│   ├── main.tsx                  # React 挂载入口
│   ├── App.tsx                   # 根路由与全局 Provider
│   ├── pages/                    # 页面视图
│   │   ├── WorkspacePage.tsx     # 主交互工作台（实时对话 / 翻唱 / 形象舞台）
│   │   ├── OnboardingPage.tsx    # 引导录音建档向导页面
│   │   └── SettingsPage.tsx      # 部署模式切换、服务器地址与曲库管理
│   ├── audio/                    # 【核心边缘音频引擎】
│   │   ├── vad/
│   │   │   ├── sileroVad.ts      # ONNX Runtime Web Silero VAD 封装
│   │   │   └── bargeIn.ts        # 本地 <10ms 打断检测与 gen_id 自增触发器
│   │   ├── protocol/
│   │   │   ├── binaryFrame.ts    # 16-Byte Big-Endian 帧打包/解包与 gen_id 过滤
│   │   │   └── signaling.ts      # HELLO 握手、INTERRUPT 与情绪/唱歌信令类型
│   │   ├── worklets/
│   │   │   ├── captureProcessor.ts  # 麦克风采集 -> PCM16 / Opus 帧切片 (20ms)
│   │   │   └── playbackProcessor.ts # 流式抖动缓冲 (Jitter Buffer) 与 PTS 播放
│   │   ├── transport/
│   │   │   ├── wsClient.ts       # WebSocket 双工通道（支持本地 PCM 与 LAN Opus）
│   │   │   └── webrtcClient.ts   # WebRTC (UDP) 媒体通道封装
│   │   ├── mixer.ts              # Web Audio API 双轨混音（纯人声流 + 强缓存伴奏）
│   │   └── recorder.ts           # 引导录音（15s 朗读 + 10s 清唱）与 FLAC/WAV 导出
│   ├── avatar/                   # 【预留虚拟形象模块】
│   │   ├── types.ts              # AvatarRenderer 统一接口 (live2d / vrm / none)
│   │   ├── lipSync.ts            # 接收 0x04 帧并按 PTS 对齐 Viseme / RMS 包络
│   │   ├── live2dRenderer.ts     # 预留：PixiJS Live2D 渲染与表情驱动
│   │   └── vrmRenderer.ts        # 预留：Three.js 3D VRM 渲染与动作驱动
│   ├── components/               # 纯 UI 展示与交互组件
│   │   ├── onboarding/           # 录音向导、波形提示与 ASR 标注预览
│   │   ├── chat/                 # 实时对话字幕、情绪标签展示与打断状态指示
│   │   ├── singing/              # 伴奏选择、歌词同步面板、人声/伴奏音量推子
│   │   └── character/            # 角色切换卡片、后台微调进度条 (SSE/WS)
│   ├── services/                 # REST API 封装 (/v1/system, /assets, /characters, /songs)
│   ├── stores/                   # 全局状态管理（连接模式、当前角色、gen_id、混音参数）
│   └── types/                    # TypeScript 公共类型定义
├── package.json
└── tsconfig.json
```

---

## 3. 后端目录结构与职责 (`backend/`)

采用 **网关层 (`api`/`ws`) + 调度核心层 (`core`) + 模型引擎层 (`engines`) + 异步任务层 (`workers`)** 四层架构，确保显存调度与模型推理逻辑不耦合 HTTP/WS 传输细节。

```text
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI 入口、Lifespan 生命周期（模型预热与缓存释放）
│   ├── config.py                 # Pydantic Settings（显存上限、deploy_mode、模型与存储路径）
│   ├── api/                      # REST 接口层
│   │   ├── __init__.py
│   │   ├── deps.py               # 依赖注入（DB 会话、GPUManager、SpkCache）
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py         # /v1 路由聚合
│   │       ├── system.py         # GET /v1/system/capabilities
│   │       ├── assets.py         # POST /v1/assets/upload-check & /upload
│   │       ├── characters.py     # POST /v1/characters/register & 档案 CRUD
│   │       ├── finetune.py       # POST /v1/finetune/jobs & SSE/WS 进度流
│   │       └── songs.py          # GET /v1/songs/{id}/instrumental (206 Range + ETag)
│   ├── ws/                       # 实时双工通信层
│   │   ├── __init__.py
│   │   ├── session.py            # /v1/session WebSocket & WebRTC (aiortc) 会话调度
│   │   ├── protocol.py           # 16-Byte Big-Endian 帧编解码 (struct) 与 gen_id 校验
│   │   └── negotiation.py        # HELLO 握手策略（Local Raw PCM vs LAN Opus）
│   ├── core/                     # 资源调度与全双工流水线编排
│   │   ├── __init__.py
│   │   ├── gpu_manager.py        # 24GB 单卡显存调度（对话常驻/唱歌错峰 + Pinned Memory 热切换）
│   │   ├── spk_cache.py          # 活跃角色 Speaker Embedding 内存常驻字典
│   │   └── pipeline.py           # ASR -> LLM 路由 -> TTS/VC -> 16B 帧下发实时流水线
│   ├── engines/                  # AI 模型推理与音频处理引擎（统一抽象接口）
│   │   ├── __init__.py
│   │   ├── base.py               # 引擎生命周期基类（load_to_gpu / offload_to_pinned_cpu）
│   │   ├── asr_engine.py         # SenseVoice / Faster-Whisper 实时识别与引导录音标注
│   │   ├── llm_router.py         # vLLM / Ollama 客户端（[emotion] 解析 + trigger_singing）
│   │   ├── tts_engine.py         # CosyVoice 3 / Qwen3-TTS 流式合成 + LoRA 热挂载
│   │   ├── vc_engine.py          # Seed-VC 零样本转换（仅输出纯人声干声流）
│   │   ├── separator.py          # python-audio-separator (BS-RoFormer) 曲库分离
│   │   └── lip_sync.py           # 预留：Viseme 口型权重与 RMS 包络提取 (0x04 帧)
│   ├── db/                       # 数据持久化
│   │   ├── __init__.py
│   │   ├── session.py            # 异步数据库连接与初始化
│   │   └── models.py             # ORM 模型（character_profiles 含 avatar_* 预留字段）
│   ├── schemas/                  # Pydantic 请求/响应与内部 DTO 模型
│   │   ├── __init__.py
│   │   ├── character.py
│   │   ├── protocol.py
│   │   └── song.py
│   └── workers/                  # Redis / RQ 后台异步队列执行器
│       ├── __init__.py
│       ├── rq_app.py             # Redis 连接与队列定义
│       ├── finetune_worker.py    # Qwen3-TTS LoRA / GPT-SoVITS / Seed-VC 异步微调
│       └── separator_worker.py   # 曲库批量离线分离与 Opus 伴奏转码
├── tests/                        # 自动化测试
│   ├── __init__.py
│   ├── test_protocol_16b.py      # 16-Byte 帧打包解包与 Big-Endian 边界测试
│   ├── test_gpu_manager.py       # 显存互斥调度与 Pinned Memory 切换模拟测试
│   └── test_api_v1.py            # REST 接口与秒传/Range 响应测试
└── pyproject.toml                # Python 依赖、Ruff/Pytest 配置
```

---

## 4. 代码与工程规范约定

### 4.1 命名规范
- **前端 (TypeScript / React)**：
  - 目录名使用全小写短横线或单词：`audio/worklets/`, `components/onboarding/`。
  - 组件文件使用 `PascalCase.tsx`（如 `OnboardingWizard.tsx`），工具/协议/引擎模块使用 `camelCase.ts`（如 `binaryFrame.ts`、`sileroVad.ts`）。
  - 类型与接口使用 `PascalCase`，常量枚举使用 `UPPER_SNAKE_CASE`。
- **后端 (Python 3.11+)**：
  - 目录与模块统一使用 `snake_case.py`（如 `gpu_manager.py`、`tts_engine.py`）。
  - 类名使用 `PascalCase`，函数与变量使用 `snake_case`，常量使用 `UPPER_SNAKE_CASE`。
  - 所有 I/O 与推理网关接口优先使用 `async def`，CPU/GPU 密集型同步调用须通过 `asyncio.to_thread` 或专用线程池执行，避免阻塞事件循环。

### 4.2 协议与跨端一致性红线
1. **禁止 Base64 传输实时音频**：所有实时语音、TTS、VC 干声与口型帧均须通过 `16-Byte` 二进制帧头（Big-Endian `>BBHIII`）传输。
2. **打断防残留 (`gen_id`)**：前端在本地 VAD 触发 Barge-in 时自增 `gen_id` 并发送 `INTERRUPT`；前后端均须丢弃 `frame.gen_id < current_gen_id` 的滞留音频帧。
3. **时间戳对齐 (`PTS`)**：VC 纯人声干声帧（`0x03`）与预留口型同步帧（`0x04`）必须携带一致的毫秒级 `PTS`，保证前端 `mixer.ts` 双轨混音与 `lipSync.ts` 口型渲染对齐。
4. **本地 vs 局域网双模透明**：业务逻辑层不感知传输编码差异；编码自适应（本地 `PCM16` / 局域网 `Opus`）与文件上传策略（本地绝对路径直传 / 局域网 `FLAC + SHA-256` 秒传）统一收敛在 `ws/negotiation.py` 与 `api/v1/assets.py`。

### 4.3 显存与资产安全红线
1. **单卡显存互斥**：`tts_engine.py` 与 `vc_engine.py` 严禁绕过 `core/gpu_manager.py` 直接 `.to("cuda")`，必须通过 `GPUManager` 申请显存槽位并在切换模式时执行 Pinned Memory 换入换出。
2. **路径安全**：`local_path` 直传仅限本地模式（`127.0.0.1`），且后端必须校验路径规范化与允许目录白名单，防止路径穿越。
3. **大文件不入库**：`storage/` 下的音视频素材、分离干声与 LoRA 权重文件严禁提交至 Git，仅在 `character_profiles` 数据库中记录相对路径与元信息。
