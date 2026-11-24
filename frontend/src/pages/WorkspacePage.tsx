import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { HkSelect } from "../components/common/HkSelect";
import { DualTrackMixer } from "../audio/mixer";
import { FrameFlags, FrameType } from "../audio/protocol/binaryFrame";
import { HakochuuWsClient } from "../audio/transport/wsClient";
import { BargeInController } from "../audio/vad/bargeIn";
import { SileroVadDetector } from "../audio/vad/sileroVad";
import { AudioCaptureEngine } from "../audio/worklets/captureProcessor";
import { AudioPlaybackEngine } from "../audio/worklets/playbackProcessor";
import { LipSyncController } from "../avatar/lipSync";
import {
  CharacterProfile,
  LlmModelConfig,
  listCharacters,
  listLlmModels,
} from "../services/api";

interface CapabilitiesResp {
  protocol_version: string;
  deploy_mode: string;
  mock_engines: boolean;
  supported_codecs: string[];
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  genId?: number;
  text: string;
  emotion?: string;
  time: string;
}

interface FrameTurnStats {
  genId: number;
  ttsFrames: number;
  ttsBytes: number;
  vcFrames: number;
  vcBytes: number;
}

export function WorkspacePage() {
  const audioCtxRef = useRef<AudioContext | null>(null);
  const clientRef = useRef<HakochuuWsClient | null>(null);
  const captureRef = useRef<AudioCaptureEngine | null>(null);
  const playbackRef = useRef<AudioPlaybackEngine | null>(null);
  const mixerRef = useRef<DualTrackMixer>(new DualTrackMixer(24000));
  const lipSyncRef = useRef<LipSyncController>(new LipSyncController());
  const vadRef = useRef<SileroVadDetector | null>(null);
  const bargeInRef = useRef<BargeInController | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  const frameStatsRef = useRef<FrameTurnStats>({
    genId: 0,
    ttsFrames: 0,
    ttsBytes: 0,
    vcFrames: 0,
    vcBytes: 0,
  });
  const verboseFramesRef = useRef(false);

  const [connected, setConnected] = useState(false);
  const [micActive, setMicActive] = useState(false);
  const [vadSpeaking, setVadSpeaking] = useState(false);
  const [aiPlaying, setAiPlaying] = useState(false);
  const [mouthLevel, setMouthLevel] = useState(0);
  const [genId, setGenId] = useState(1);
  const [emotionText, setEmotionText] = useState("neutral");
  const [promptInput, setPromptInput] = useState("你好，今天天气怎么样？");
  const [instVol, setInstVol] = useState(0.7);
  const [vocalVol, setVocalVol] = useState(1.0);
  const [capabilities, setCapabilities] = useState<CapabilitiesResp | null>(null);

  // 共享角色库 + 可切换 LLM 与专属参数（舞台下方可折叠两行分组表单）
  const [characters, setCharacters] = useState<CharacterProfile[]>([]);
  const [selectedCharId, setSelectedCharId] = useState("char_001");
  const [llmModels, setLlmModels] = useState<LlmModelConfig[]>([]);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [llmParams, setLlmParams] = useState<Record<string, string | number>>({});
  const [paramsOpen, setParamsOpen] = useState(true);

  // 对话消息区与底层调试台彻底分离
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [debugOpen, setDebugOpen] = useState(false);
  const [verboseFrames, setVerboseFrames] = useState(false);
  const [frameCounters, setFrameCounters] = useState({ tts: 0, vc: 0, dropped: 0 });
  const [toasts, setToasts] = useState<Array<{ id: number; component: string; message: string }>>(
    []
  );

  const activeModel = useMemo(
    () => llmModels.find((m) => m.id === selectedModelId) ?? null,
    [llmModels, selectedModelId]
  );

  const activeCharacter = useMemo(
    () => characters.find((c) => c.id === selectedCharId) ?? null,
    [characters, selectedCharId]
  );

  const controlParams = useMemo(
    () => (activeModel?.custom_params ?? []).filter((p) => p.type !== "slider"),
    [activeModel]
  );

  const sliderParams = useMemo(
    () => (activeModel?.custom_params ?? []).filter((p) => p.type === "slider"),
    [activeModel]
  );

  useEffect(() => {
    verboseFramesRef.current = verboseFrames;
  }, [verboseFrames]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  const appendLog = (entry: string) => {
    setLogs((prev) => [`[${new Date().toLocaleTimeString()}] ${entry}`, ...prev].slice(0, 80));
  };

  const appendSystemChat = (text: string) => {
    setChatMessages((prev) => [
      ...prev,
      {
        id: `sys-${Date.now()}-${Math.random()}`,
        role: "system",
        text,
        time: new Date().toLocaleTimeString(),
      },
    ]);
  };

  const upsertAssistantChunk = (turnGenId: number, chunkText: string, emotion?: string) => {
    const time = new Date().toLocaleTimeString();
    setChatMessages((prev) => {
      const idx = prev.findIndex((m) => m.role === "assistant" && m.genId === turnGenId);
      if (idx === -1) {
        return [
          ...prev,
          {
            id: `ai-${turnGenId}-${Date.now()}`,
            role: "assistant",
            genId: turnGenId,
            text: chunkText,
            emotion,
            time,
          },
        ];
      }
      const next = [...prev];
      const existing = next[idx];
      const sep = existing.text.endsWith("\n") ? "" : " ";
      next[idx] = {
        ...existing,
        text: `${existing.text}${sep}${chunkText}`,
        emotion: emotion ?? existing.emotion,
        time,
      };
      return next;
    });
  };

  const flushTurnFrameSummary = (turnGenId: number) => {
    const st = frameStatsRef.current;
    if (st.genId !== turnGenId) return;
    if (st.ttsFrames > 0) {
      appendLog(
        `0x02 TTS 流汇总 (gen_id=${turnGenId}): 共 ${st.ttsFrames} 帧 / ${st.ttsFrames * 20}ms (${st.ttsBytes}B)`
      );
    }
    if (st.vcFrames > 0) {
      appendLog(
        `0x03 VC 干声汇总 (gen_id=${turnGenId}): 共 ${st.vcFrames} 帧 / ${st.vcFrames * 20}ms (${st.vcBytes}B)`
      );
    }
    frameStatsRef.current = { genId: turnGenId, ttsFrames: 0, ttsBytes: 0, vcFrames: 0, vcBytes: 0 };
  };

  const pushToast = (component: string, message: string) => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((prev) => [...prev, { id, component, message }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  };

  const applyModelDefaults = (model: LlmModelConfig) => {
    const defaults: Record<string, string | number> = {};
    for (const p of model.custom_params) {
      defaults[p.key] = p.default;
    }
    setLlmParams(defaults);
  };

  useEffect(() => {
    fetch("/v1/system/capabilities")
      .then((r) => r.json())
      .then((data: CapabilitiesResp) => setCapabilities(data))
      .catch(() => appendLog("后端 /v1/system/capabilities 暂不可达（请确认 FastAPI 已启动）"));

    listCharacters()
      .then((chars) => {
        setCharacters(chars);
        if (chars.length > 0) {
          setSelectedCharId(chars[0].id);
        }
      })
      .catch(() => {});

    listLlmModels()
      .then((models) => {
        setLlmModels(models);
        if (models.length > 0) {
          setSelectedModelId(models[0].id);
          applyModelDefaults(models[0]);
        }
      })
      .catch(() => {});

    return () => {
      void captureRef.current?.stop();
      playbackRef.current?.dispose();
      mixerRef.current.stopSinging();
      lipSyncRef.current.reset();
      clientRef.current?.disconnect();
    };
  }, []);

  const ensureAudioReady = async (): Promise<AudioContext> => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AudioContext({ sampleRate: 24000, latencyHint: "interactive" });
    }
    if (audioCtxRef.current.state === "suspended") {
      await audioCtxRef.current.resume();
    }
    if (playbackRef.current) {
      await playbackRef.current.init(audioCtxRef.current);
    }
    await mixerRef.current.ensureContext(audioCtxRef.current);
    return audioCtxRef.current;
  };

  const handleModelChange = (nextId: string) => {
    setSelectedModelId(nextId);
    const found = llmModels.find((m) => m.id === nextId);
    if (found) {
      applyModelDefaults(found);
      appendLog(`已切换 LLM: ${found.display_name}`);
    }
  };

  const handleConnect = () => {
    const playback = new AudioPlaybackEngine((playing) => setAiPlaying(playing));
    playbackRef.current = playback;
    let latestEmotion = "neutral";

    const client = new HakochuuWsClient({
      onOpen: () => {
        setConnected(true);
        client.sendSignal({
          type: "HELLO",
          protocol_version: "1.0",
          deploy_mode: "local",
          capabilities: ["wasm_vad", "client_mixer", "avatar_viseme_v1", "asr_duplex"],
          echo_loopback: false,
          character_id: selectedCharId,
          llm_model_id: selectedModelId || undefined,
          llm_params: llmParams,
        });
        appendLog(`WebSocket 已连接 (角色=${selectedCharId}, 模型=${selectedModelId || "默认"})`);
      },
      onClose: () => {
        setConnected(false);
        appendLog("WebSocket 已断开");
      },
      onSignal: (msg) => {
        const msgType = String(msg.type ?? "");
        const msgGenId = typeof msg.gen_id === "number" ? msg.gen_id : client.genId;

        if (msgType === "EMOTION_TAG") {
          const emo = String(msg.emotion ?? "neutral");
          const act = msg.action ? ` / ${String(msg.action)}` : "";
          latestEmotion = `${emo}${act}`;
          setEmotionText(latestEmotion);
          appendLog(`情绪标签: ${latestEmotion} (gen_id=${msgGenId})`);
          return;
        }
        if (msgType === "ASR_RESULT") {
          const text = String(msg.text ?? "").trim();
          if (text) {
            setChatMessages((prev) => [
              ...prev,
              {
                id: `asr-${msgGenId}-${Date.now()}`,
                role: "user",
                genId: msgGenId,
                text,
                emotion: "VOICE",
                time: new Date().toLocaleTimeString(),
              },
            ]);
            appendLog(`ASR_RESULT (gen_id=${msgGenId}): ${text}`);
          }
          return;
        }
        if (msgType === "LLM_TEXT") {
          const text = String(msg.text ?? "").trim();
          if (text) {
            upsertAssistantChunk(msgGenId, text, latestEmotion);
            appendLog(`LLM_TEXT (gen_id=${msgGenId}): ${text}`);
          }
          return;
        }
        if (msgType === "SING_START") {
          const songId = String(msg.song_id ?? "song_demo");
          appendSystemChat(`开始翻唱 · ${songId}`);
          appendLog(`收到信令: ${JSON.stringify(msg)}`);
          void mixerRef.current
            .startSinging(songId, msgGenId, audioCtxRef.current ?? undefined)
            .catch((err) => {
              appendLog(
                `伴奏加载跳过 (${songId}): ${err instanceof Error ? err.message : String(err)}`
              );
            });
          return;
        }
        if (msgType === "TURN_DONE") {
          flushTurnFrameSummary(msgGenId);
          appendLog(`回合结束 TURN_DONE (gen_id=${msgGenId})`);
          return;
        }
        if (msgType === "DEBUG_WARNING") {
          const comp = String(msg.component ?? "ENGINE");
          const warnMsg = String(msg.message ?? "[Debug] 缺少语音模型");
          pushToast(comp, warnMsg);
          appendSystemChat(`[${comp}] ${warnMsg}`);
          appendLog(`WARNING ${warnMsg}`);
          return;
        }
        appendLog(`收到信令: ${JSON.stringify(msg)}`);
      },
      onBinaryFrame: (frame) => {
        if (frame.frameType === FrameType.AVATAR_VISEME_DOWN) {
          const weights = lipSyncRef.current.ingestFramePayload(frame.payload, frame.ptsMs);
          setMouthLevel(Math.round(weights.rms * 100));
          return;
        }

        if (frameStatsRef.current.genId !== frame.genId) {
          frameStatsRef.current = {
            genId: frame.genId,
            ttsFrames: 0,
            ttsBytes: 0,
            vcFrames: 0,
            vcBytes: 0,
          };
        }

        if (frame.frameType === FrameType.VC_VOCAL_DOWN) {
          mixerRef.current.pushVocalFrame(frame.payload, frame.genId, frame.ptsMs);
          frameStatsRef.current.vcFrames += 1;
          frameStatsRef.current.vcBytes += frame.payload.byteLength;
          setFrameCounters((c) => ({ ...c, vc: c.vc + 1 }));

          if (verboseFramesRef.current || frameStatsRef.current.vcFrames === 1) {
            appendLog(
              `收到 0x03 VC 干声帧: gen_id=${frame.genId} pts=${frame.ptsMs}ms (${frame.payload.byteLength}B)`
            );
          }
          return;
        }

        if (frame.frameType === FrameType.TTS_AUDIO_DOWN) {
          if (audioCtxRef.current) {
            playback.enqueuePcm16(frame.payload, frame.genId, frame.ptsMs);
          }
          frameStatsRef.current.ttsFrames += 1;
          frameStatsRef.current.ttsBytes += frame.payload.byteLength;
          setFrameCounters((c) => ({ ...c, tts: c.tts + 1 }));

          if (verboseFramesRef.current || frameStatsRef.current.ttsFrames === 1) {
            appendLog(
              `收到 0x02 TTS 帧: gen_id=${frame.genId} pts=${frame.ptsMs}ms (${frame.payload.byteLength}B)`
            );
          }
        }
      },
      onDropStale: (frame) => {
        setFrameCounters((c) => ({ ...c, dropped: c.dropped + 1 }));
        appendLog(`丢弃滞留帧: gen_id=${frame.genId} < active=${client.genId}`);
      },
      onError: (err) => {
        appendSystemChat(`连接异常 · ${err}`);
        appendLog(`错误: ${err}`);
      },
    });
    clientRef.current = client;
    client.connect("local", selectedCharId);
  };

  const handleToggleMic = async () => {
    if (micActive) {
      await captureRef.current?.stop();
      vadRef.current?.reset();
      bargeInRef.current?.reset();
      setMicActive(false);
      setVadSpeaking(false);
      appendLog("已停止麦克风全双工采集");
      return;
    }

    if (!clientRef.current || !playbackRef.current) {
      appendLog("请先连接 /v1/session");
      return;
    }

    const vad = new SileroVadDetector();
    const vadMode = await vad.init();
    vadRef.current = vad;

    const bargeIn = new BargeInController(clientRef.current, playbackRef.current, {
      onBargeIn: (nextGenId) => {
        mixerRef.current.stopSinging(nextGenId);
        setGenId(nextGenId);
        appendLog(`触发本地 VAD 打断 (Barge-in): 清空播音缓冲，gen_id -> ${nextGenId}`);
      },
    });
    bargeInRef.current = bargeIn;

    const capture = new AudioCaptureEngine(24000, 20);
    captureRef.current = capture;

    let wasSpeaking = false;
    let silenceFrames = 0;
    const TRAILING_SILENCE_FRAMES = 15;

    try {
      const ctx = await capture.start(async (frame) => {
        const res = await vad.processFrame(frame.float32, frame.rms);
        setVadSpeaking(res.isSpeech);
        bargeIn.handleVadFrame(res.isSpeech);

        if (res.isSpeech) {
          wasSpeaking = true;
          silenceFrames = 0;
          clientRef.current?.sendAudioFrame(frame.pcm16, frame.ptsMs);
        } else if (wasSpeaking) {
          silenceFrames += 1;
          clientRef.current?.sendAudioFrame(frame.pcm16, frame.ptsMs);
          if (silenceFrames >= TRAILING_SILENCE_FRAMES) {
            wasSpeaking = false;
            silenceFrames = 0;
            clientRef.current?.sendAudioFrame(new Uint8Array(0), frame.ptsMs, {
              flags: FrameFlags.EOS,
            });
            appendLog(`VAD 说话结束，发送 EOS 触发 ASR (gen_id=${clientRef.current?.genId ?? genId})`);
          }
        }
      }, audioCtxRef.current ?? undefined);
      audioCtxRef.current = ctx;
      await playbackRef.current.init(ctx);
      await mixerRef.current.ensureContext(ctx);
      setMicActive(true);
      appendLog(`麦克风采集已启动 (采样率=${ctx.sampleRate}Hz, VAD=${vadMode})`);
    } catch (err) {
      appendLog(`麦克风启动失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleSendTextTurn = async (textOverride?: string) => {
    if (!clientRef.current) return;
    const text = (textOverride ?? promptInput).trim();
    if (!text) return;

    await ensureAudioReady();
    const turnGenId = clientRef.current.genId;

    setChatMessages((prev) => [
      ...prev,
      {
        id: `usr-${turnGenId}-${Date.now()}`,
        role: "user",
        genId: turnGenId,
        text,
        time: new Date().toLocaleTimeString(),
      },
    ]);

    clientRef.current.sendSignal({
      type: "TEXT_TURN",
      gen_id: turnGenId,
      text,
      character_id: selectedCharId,
      llm_model_id: selectedModelId || undefined,
      llm_params: llmParams,
    });
    appendLog(`发送 TEXT_TURN (gen_id=${turnGenId}, 模型=${selectedModelId || "默认"}): "${text}"`);

    if (!textOverride) {
      setPromptInput("");
    }
  };

  const handleSendTestFrame = () => {
    const payload = new TextEncoder().encode("hakochuu-pcm-test");
    const pts = Math.floor(performance.now());
    clientRef.current?.sendAudioFrame(payload, pts, { flags: FrameFlags.KEYFRAME });
    appendLog(`发送 16B 上行帧: gen_id=${clientRef.current?.genId ?? genId} pts=${pts}ms`);
  };

  const handleInterrupt = () => {
    if (!clientRef.current) return;
    const next = clientRef.current.interrupt();
    playbackRef.current?.flush(next);
    mixerRef.current.stopSinging(next);
    setGenId(next);
    appendLog(`手动触发打断 (INTERRUPT): gen_id -> ${next}`);
  };

  const displayCharName = activeCharacter?.name ?? "Default Persona";
  const displayAvatarType = (activeCharacter?.avatar_type ?? "none").toUpperCase();

  return (
    <section className="hk-container-wide">
      {/* 右上角 DEBUG_WARNING 降级提示 */}
      {toasts.length > 0 ? (
        <div className="hk-toast-stack" role="status" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className="hk-toast">
              <div>
                <span className="hk-tag-brace" style={{ marginRight: "0.45rem" }}>
                  {t.component}
                </span>
                <span>{t.message}</span>
              </div>
              <button
                type="button"
                className="hk-toast__close"
                onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
                aria-label="关闭提示"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : null}

      {/* 主视觉画廊两栏：左虚拟形象舞台 + 右实时对话手记 */}
      <div className="hk-stage-grid">
        {/* 左侧：虚拟形象预留舞台与声学控制 */}
        <div className="hk-stage-column hk-reveal">
          <div className="hk-avatar-frame">
            <span className="hk-tag-ribbon hk-tag-ribbon--overhang">{displayCharName}</span>

            <div className="hk-avatar-frame__placeholder">
              <svg
                className="hk-avatar-frame__emblem hk-motion-float"
                viewBox="0 0 64 64"
                fill="none"
                stroke="currentColor"
                strokeWidth="0.9"
                aria-hidden="true"
              >
                <path d="M32 8C36 18 46 28 56 32C46 36 36 46 32 56C28 46 18 36 8 32C18 28 28 18 32 8Z" />
                <circle cx="32" cy="32" r="6" />
              </svg>
              <div className="hk-avatar-frame__caption">AVATAR STAGE</div>
              <div className="hk-tag-brace">{displayAvatarType}</div>
              <div className="hk-secondary" style={{ fontSize: "var(--text-xs)", maxWidth: 240 }}>
                预留 Live2D / VRM 渲染画布 · 同步 0x04 口型帧与情绪标签
              </div>
            </div>

            <div className="hk-avatar-frame__meta">
              <div className="hk-avatar-frame__meta-row">
                <span className="hk-tag-brace">{emotionText}</span>
                <span className="hk-num" style={{ color: "var(--color-ink-primary)" }}>
                  LIP 0x04 · {mouthLevel}%
                </span>
              </div>
              <div className="hk-meter">
                <div className="hk-meter__fill" style={{ width: `${mouthLevel}%` }} />
              </div>
            </div>
          </div>

          <div className="hk-divider-diamond">
            <span className="hk-divider-diamond__line" />
          </div>

          {/* 实时语音全双工遥测 */}
          <div className="hk-telemetry-strip">
            <div className="hk-telemetry-item">
              <span className="hk-telemetry-item__label">GEN ID</span>
              <span className="hk-telemetry-item__value hk-num">#{genId}</span>
            </div>
            <div className="hk-telemetry-item">
              <span className="hk-telemetry-item__label">VAD</span>
              <span className="hk-telemetry-item__value">{vadSpeaking ? "说话中" : "静音"}</span>
            </div>
            <div className="hk-telemetry-item">
              <span className="hk-telemetry-item__label">PLAYBACK</span>
              <span className="hk-telemetry-item__value">{aiPlaying ? "播音中" : "空闲"}</span>
            </div>
          </div>

          {/* 翻唱双轨混音细线推子 */}
          <div className="hk-mixer-block">
            <label className="hk-mixer-channel">
              <span className="hk-mixer-channel__head">
                <span>伴奏音轨</span>
                <span className="hk-num hk-mixer-channel__val">{Math.round(instVol * 100)}%</span>
              </span>
              <input
                type="range"
                className="hk-range"
                min={0}
                max={1.2}
                step={0.05}
                value={instVol}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setInstVol(v);
                  mixerRef.current.setInstrumentalVolume(v);
                }}
              />
            </label>

            <label className="hk-mixer-channel">
              <span className="hk-mixer-channel__head">
                <span>人声干声</span>
                <span className="hk-num hk-mixer-channel__val">{Math.round(vocalVol * 100)}%</span>
              </span>
              <input
                type="range"
                className="hk-range"
                min={0}
                max={1.5}
                step={0.05}
                value={vocalVol}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setVocalVol(v);
                  mixerRef.current.setVocalVolume(v);
                }}
              />
            </label>
          </div>
        </div>

        {/* 右侧：实时对话手记与全双工会话控制 */}
        <div className="hk-dialogue-column hk-reveal hk-reveal--delay-1">
          <div className="hk-session-bar">
            <div className="hk-session-status">
              <span
                className={`hk-status-diamond ${
                  connected ? "hk-status-diamond--ok" : "hk-status-diamond--down"
                }`}
              />
              <span className="hk-serif" style={{ letterSpacing: "0.12em" }}>
                {connected ? "SESSION LIVE" : "STANDBY"}
              </span>
              {capabilities ? (
                <span className="hk-tag-brace">
                  {capabilities.deploy_mode}
                  {capabilities.mock_engines ? " · MOCK" : ""}
                </span>
              ) : (
                <span className="hk-tag-brace">GATEWAY OFFLINE</span>
              )}
            </div>

            <div className="hk-session-actions">
              {!connected ? (
                <button type="button" className="hk-btn hk-btn--primary" onClick={handleConnect}>
                  连接会话
                </button>
              ) : (
                <button
                  type="button"
                  className="hk-btn"
                  onClick={() => clientRef.current?.disconnect()}
                >
                  断开连接
                </button>
              )}

              <button
                type="button"
                className={`hk-btn ${micActive ? "hk-btn--primary" : ""}`}
                onClick={() => void handleToggleMic()}
                disabled={!connected}
              >
                {micActive ? "停止全双工" : "麦克风全双工"}
              </button>

              <button
                type="button"
                className="hk-btn hk-btn--ghost"
                onClick={handleInterrupt}
                disabled={!connected}
              >
                打断
              </button>

              <button
                type="button"
                className="hk-btn hk-btn--ghost"
                onClick={handleSendTestFrame}
                disabled={!connected}
              >
                16B 测试帧
              </button>
            </div>
          </div>

          {/* 对话消息流 */}
          <div className="hk-chat-stream">
            {chatMessages.length === 0 ? (
              <div className="hk-chat-empty">
                <p style={{ margin: "0 0 0.4rem 0" }}>点击「连接会话」以开启实时语音与翻唱对话。</p>
                <span className="hk-tag-brace">16-BYTE PCM · FULL-DUPLEX</span>
              </div>
            ) : null}

            {chatMessages.map((msg) => (
              <div key={msg.id} className={`hk-chat-entry hk-chat-entry--${msg.role}`}>
                {msg.role !== "system" ? (
                  <div className="hk-chat-entry__meta">
                    <span className="hk-serif" style={{ letterSpacing: "0.1em" }}>
                      {msg.role === "user" ? "YOU" : displayCharName.toUpperCase()}
                    </span>
                    <span className="hk-num">{msg.time}</span>
                    {msg.emotion ? <span className="hk-tag-brace">{msg.emotion}</span> : null}
                  </div>
                ) : null}

                {msg.role === "system" ? (
                  <div className="hk-divider-diamond">
                    <span className="hk-divider-diamond__line" />
                    <span className="hk-chat-entry__body">{msg.text}</span>
                    <span className="hk-divider-diamond__line" />
                  </div>
                ) : (
                  <div className="hk-chat-entry__body">{msg.text}</div>
                )}
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>

          {/* 底部发送栏（单行齐平） */}
          <div className="hk-composer">
            <input
              type="text"
              className="hk-input"
              value={promptInput}
              onChange={(e) => setPromptInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && connected) {
                  e.preventDefault();
                  void handleSendTextTurn();
                }
              }}
              placeholder="输入对话内容触发 TTS，或输入点歌指令（回车发送）..."
            />
            <button
              type="button"
              className="hk-btn-submit hk-composer__send"
              onClick={() => void handleSendTextTurn()}
              disabled={!connected}
            >
              发送对话
            </button>
            <button
              type="button"
              className="hk-btn hk-btn--ghost hk-composer__sing"
              onClick={() => void handleSendTextTurn("帮我唱一首晴天")}
              disabled={!connected}
            >
              点歌测试
            </button>
          </div>
        </div>
      </div>

      {/* 舞台下方：可折叠两行分组参数表单 */}
      <div className="hk-params-drawer hk-reveal hk-reveal--delay-2">
        <div className="hk-divider-diamond">
          <span className="hk-divider-diamond__line" />
        </div>

        <button
          type="button"
          className="hk-params-toggle hk-motion-fade"
          onClick={() => setParamsOpen((v) => !v)}
          aria-expanded={paramsOpen}
        >
          <span className="hk-params-toggle__left">
            <span className="hk-params-toggle__title">PERSONA &amp; MODEL PARAMETERS</span>
            <span className="hk-tag-brace">{displayCharName}</span>
            <span className="hk-tag-brace">{activeModel?.display_name ?? "DEFAULT LLM"}</span>
          </span>
          <span
            className="hk-serif hk-secondary"
            style={{ fontSize: "var(--text-xs)", letterSpacing: "0.14em" }}
          >
            {paramsOpen ? "HIDE −" : "EXPAND +"}
          </span>
        </button>

        {paramsOpen ? (
          <div className="hk-params-body">
            {/* 第一行：4 列等宽下拉与文本输入框 */}
            <div className="hk-params-row-controls">
              <div className="hk-field">
                <span className="hk-field__label">共享角色档案</span>
                <HkSelect
                  value={selectedCharId}
                  ariaLabel="共享角色档案"
                  options={
                    characters.length === 0
                      ? [{ value: "char_001", label: "默认角色 (char_001)" }]
                      : characters.map((c) => ({
                          value: c.id,
                          label: `${c.name} (${c.id})`,
                        }))
                  }
                  onChange={setSelectedCharId}
                />
              </div>

              <div className="hk-field">
                <span className="hk-field__label">对话 LLM 模型</span>
                <HkSelect
                  value={selectedModelId}
                  ariaLabel="对话 LLM 模型"
                  options={
                    llmModels.length === 0
                      ? [{ value: "", label: "默认 LLM" }]
                      : llmModels.map((m) => ({
                          value: m.id,
                          label: m.display_name,
                        }))
                  }
                  onChange={handleModelChange}
                />
              </div>

              {controlParams.map((param) => {
                const val = llmParams[param.key] ?? param.default;
                if (param.type === "select" && param.options) {
                  const selectedOpt = param.options.find((o) => o.value === val);
                  return (
                    <div key={param.key} className="hk-field">
                      <span className="hk-field__label">{param.label}</span>
                      <HkSelect
                        value={String(val)}
                        ariaLabel={param.label}
                        options={param.options.map((opt) => ({
                          value: opt.value,
                          label: opt.label,
                        }))}
                        onChange={(nextVal) =>
                          setLlmParams((prev) => ({ ...prev, [param.key]: nextVal }))
                        }
                      />
                      {selectedOpt?.style ? (
                        <span className="hk-secondary" style={{ fontSize: "var(--text-xs)" }}>
                          {selectedOpt.style}
                        </span>
                      ) : null}
                    </div>
                  );
                }
                if (param.type === "text") {
                  return (
                    <label key={param.key} className="hk-field">
                      <span className="hk-field__label">{param.label}</span>
                      <input
                        type="text"
                        className="hk-input"
                        value={String(val)}
                        placeholder={param.placeholder ?? ""}
                        onChange={(e) =>
                          setLlmParams((prev) => ({ ...prev, [param.key]: e.target.value }))
                        }
                      />
                    </label>
                  );
                }
                return null;
              })}
            </div>

            {/* 第二行：滑块参数并排基线对齐 */}
            {sliderParams.length > 0 ? (
              <div className="hk-params-row-sliders">
                {sliderParams.map((param) => {
                  const val = llmParams[param.key] ?? param.default;
                  return (
                    <label key={param.key} className="hk-mixer-channel">
                      <span className="hk-mixer-channel__head">
                        <span>{param.label}</span>
                        <span className="hk-num hk-mixer-channel__val">{String(val)}</span>
                      </span>
                      <input
                        type="range"
                        className="hk-range"
                        min={param.min ?? 0}
                        max={param.max ?? 1}
                        step={param.step ?? 0.05}
                        value={Number(val)}
                        onChange={(e) =>
                          setLlmParams((prev) => ({
                            ...prev,
                            [param.key]: Number(e.target.value),
                          }))
                        }
                      />
                    </label>
                  );
                })}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {/* 底部：可折叠底层协议与音频帧调试台 */}
      <div className="hk-debug-section hk-reveal hk-reveal--delay-3">
        <div className="hk-divider-diamond">
          <span className="hk-divider-diamond__line" />
        </div>

        <div className="hk-debug-bar">
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.95rem" }}>
            <span
              className="hk-serif"
              style={{ letterSpacing: "0.14em", color: "var(--color-ink-primary)", fontWeight: 500 }}
            >
              PROTOCOL INSPECTOR
            </span>
            <span className="hk-num">TTS {frameCounters.tts}</span>
            <span className="hk-num">VC {frameCounters.vc}</span>
            <span className="hk-num">DROPPED {frameCounters.dropped}</span>
          </div>

          <div className="hk-debug-controls">
            <label className="hk-checkbox">
              <input
                type="checkbox"
                checked={verboseFrames}
                onChange={(e) => setVerboseFrames(e.target.checked)}
              />
              逐帧日志
            </label>

            <button
              type="button"
              className="hk-btn hk-btn--ghost hk-btn--sm"
              onClick={() => {
                setLogs([]);
                setFrameCounters({ tts: 0, vc: 0, dropped: 0 });
              }}
            >
              清空
            </button>

            <button
              type="button"
              className="hk-btn hk-btn--ghost hk-btn--sm"
              onClick={() => setDebugOpen((v) => !v)}
            >
              {debugOpen ? "收起调试台" : "展开调试台"}
            </button>

            <Link to="/logs" className="hk-cta-link">
              <span>CHRONICLE</span>
              <span className="hk-btn-circle hk-btn-circle--sm" aria-hidden="true">
                <span className="hk-btn-circle__arrow">→</span>
              </span>
            </Link>
          </div>
        </div>

        {debugOpen ? (
          <div className="hk-terminal" style={{ minHeight: 150, maxHeight: 240 }}>
            {logs.length === 0 ? (
              <div style={{ opacity: 0.72 }}>暂无底层调试日志。</div>
            ) : (
              logs.map((line, i) => (
                <div key={i} className="hk-terminal__line">
                  {line}
                </div>
              ))
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
