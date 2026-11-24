import { useEffect, useRef, useState } from "react";
import { GuidedAudioRecorder, RecordedClip } from "../audio/recorder";
import { AudioFilePicker } from "../components/common/AudioFilePicker";
import { HkSelect } from "../components/common/HkSelect";
import {
  CharacterProfile,
  deleteCharacter,
  listCharacters,
  registerCharacter,
  uploadAssetFile,
  uploadCheck,
} from "../services/api";

const PROMPT_TEXT =
  "今天阳光真好呀！风吹过树梢沙沙作响，我们一起去海边听听海浪的声音，再聊聊最近的计划吧。";

type VoiceInputMode = "upload" | "record";

async function fileToClip(file: File): Promise<RecordedClip> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  const sha256 = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  const url = URL.createObjectURL(file);

  let durationSec = 0;
  try {
    durationSec = await new Promise<number>((resolve) => {
      const audio = new Audio();
      audio.preload = "metadata";
      audio.onloadedmetadata = () => resolve(Number.isFinite(audio.duration) ? audio.duration : 0);
      audio.onerror = () => resolve(0);
      audio.src = url;
    });
  } catch {
    durationSec = 0;
  }

  return {
    blob: file,
    url,
    sha256,
    durationSec,
    filename: file.name,
  };
}

export function OnboardingPage() {
  const recorderRef = useRef<GuidedAudioRecorder | null>(null);

  const [mode, setMode] = useState<VoiceInputMode>("upload");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [avatarType, setAvatarType] = useState<"none" | "live2d" | "vrm">("none");
  const [speechText, setSpeechText] = useState("");

  const [recordingTarget, setRecordingTarget] = useState<"speech" | "sing" | null>(null);
  const [level, setLevel] = useState(0);
  const [speechClip, setSpeechClip] = useState<RecordedClip | null>(null);
  const [singClip, setSingClip] = useState<RecordedClip | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [characters, setCharacters] = useState<CharacterProfile[]>([]);

  const refreshCharacters = () => {
    listCharacters()
      .then(setCharacters)
      .catch(() => {
        /* 后端未启动时忽略 */
      });
  };

  useEffect(() => {
    refreshCharacters();
  }, []);

  const handleModeSwitch = (nextMode: VoiceInputMode) => {
    if (recordingTarget !== null) return;
    setMode(nextMode);
    if (nextMode === "record" && !speechText.trim()) {
      setSpeechText(PROMPT_TEXT);
    }
  };

  const handleFileSelect = async (target: "speech" | "sing", file: File | null) => {
    if (!file) return;
    try {
      const clip = await fileToClip(file);
      if (target === "speech") {
        setSpeechClip(clip);
      } else {
        setSingClip(clip);
      }
      setStatusMsg(
        `已选择${target === "speech" ? "朗读" : "清唱"}文件：${file.name}${
          clip.durationSec > 0 ? `（${clip.durationSec.toFixed(1)}s）` : ""
        }`
      );
    } catch (err) {
      setStatusMsg(`读取音频失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const startRecording = async (target: "speech" | "sing") => {
    if (recordingTarget) return;
    const rec = new GuidedAudioRecorder();
    recorderRef.current = rec;
    try {
      await rec.start((rms) => setLevel(Math.min(1, rms * 8)));
      setRecordingTarget(target);
      setStatusMsg(
        target === "speech"
          ? "正在录制朗读片段（建议约 15 秒）..."
          : "正在录制清唱片段（建议约 10 秒）..."
      );
    } catch (err) {
      setStatusMsg(`无法开启麦克风: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const stopRecording = async () => {
    if (!recordingTarget || !recorderRef.current) return;
    const target = recordingTarget;
    const clip = await recorderRef.current.stop(
      target === "speech" ? "ref_speech.wav" : "ref_sing.wav"
    );
    setRecordingTarget(null);
    setLevel(0);
    if (target === "speech") {
      setSpeechClip(clip);
      if (!speechText.trim()) {
        setSpeechText(PROMPT_TEXT);
      }
    } else {
      setSingClip(clip);
    }
    setStatusMsg(
      `已完成${target === "speech" ? "朗读" : "清唱"}录制（${clip.durationSec.toFixed(1)}s）`
    );
  };

  const ensureUploaded = async (
    clip: RecordedClip | null,
    kind: "speech" | "sing"
  ): Promise<string | undefined> => {
    if (!clip) return undefined;
    const check = await uploadCheck([clip.sha256], kind);
    if (check.existing.includes(clip.sha256)) {
      return check.paths[clip.sha256];
    }
    const uploaded = await uploadAssetFile(clip.blob, clip.filename, clip.sha256, kind);
    return uploaded.path;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setStatusMsg("请填写角色名称");
      return;
    }
    setSubmitting(true);
    setStatusMsg("正在校验并上传音频素材...");
    try {
      const [speechPath, singPath] = await Promise.all([
        ensureUploaded(speechClip, "speech"),
        ensureUploaded(singClip, "sing"),
      ]);
      const resolvedSpeechText = speechClip
        ? speechText.trim() || (mode === "record" ? PROMPT_TEXT : undefined)
        : undefined;
      const created = await registerCharacter({
        name: name.trim(),
        description: description.trim() || undefined,
        ref_speech_path: speechPath,
        ref_speech_text: resolvedSpeechText,
        ref_sing_path: singPath,
        avatar_type: avatarType,
      });
      setStatusMsg(`建档成功！角色 ID: ${created.id}`);
      setName("");
      setDescription("");
      setSpeechText("");
      setSpeechClip(null);
      setSingClip(null);
      refreshCharacters();
    } catch (err) {
      setStatusMsg(`建档失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteCharacter = async (char: CharacterProfile) => {
    try {
      await deleteCharacter(char.id);
      setStatusMsg(`已删除角色档案：${char.name} (${char.id})`);
      refreshCharacters();
    } catch (err) {
      setStatusMsg(`删除失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  return (
    <section className="hk-container-wide">
      {/* 居中对称抬头：映射 PROFILE.png */}
      <header className="hk-page-header">
        <div className="hk-page-header__motif" aria-hidden="true">
          <svg width="26" height="18" viewBox="0 0 26 18" fill="none" stroke="currentColor" strokeWidth="1">
            <path d="M13 2C14.8 6 17.5 8.5 22 9C17.5 9.5 14.8 12 13 16C11.2 12 8.5 9.5 4 9C8.5 8.5 11.2 6 13 2Z" />
          </svg>
        </div>
        <h1 className="hk-page-header__title">PERSONA</h1>
        <p className="hk-page-header__subtitle">Character Profiles &amp; Zero-Shot Voice Onboarding</p>
      </header>

      {/* 上半区：已注册角色画廊 */}
      <div className="hk-container-profile">
        <div className="hk-section-head">
          <h2 className="hk-section-title">ARCHIVE</h2>
          <span className="hk-section-sub hk-num">{characters.length} PERSONAS</span>
        </div>

        {characters.length === 0 ? (
          <p className="hk-secondary" style={{ textAlign: "center", paddingBlock: "2rem" }}>
            暂无已注册角色档案，请在下方完成首次零样本建档。
          </p>
        ) : (
          <div className="hk-member-grid" style={{ marginTop: "1.5rem" }}>
            {characters.map((c, idx) => (
              <article key={c.id} className={`hk-reveal hk-reveal--delay-${(idx % 3) + 1}`}>
                <div className="hk-char-placeholder">
                  <span className="hk-tag-ribbon hk-tag-ribbon--overhang">
                    {c.avatar_type.toUpperCase()}
                  </span>
                  <div className="hk-char-placeholder__initial">
                    {c.name.slice(0, 1).toUpperCase()}
                  </div>
                  <div className="hk-tag-brace">{c.id}</div>
                  {c.description ? (
                    <p
                      className="hk-secondary"
                      style={{
                        fontSize: "var(--text-xs)",
                        textAlign: "center",
                        margin: "0.25rem 0 0",
                        maxWidth: 220,
                      }}
                    >
                      {c.description}
                    </p>
                  ) : null}
                </div>

                <div className="hk-member-card__bar">
                  <span className="hk-member-card__name">{c.name}</span>
                  <button
                    type="button"
                    className="hk-btn hk-btn--ghost hk-btn--sm"
                    onClick={() => void handleDeleteCharacter(c)}
                  >
                    删除
                  </button>
                </div>

                <div className="hk-char-card__meta">
                  <span>
                    <span className="hk-tag-brace">
                      {c.ref_speech_path ? "SPEECH READY" : "NO SPEECH"}
                    </span>{" "}
                    <span className="hk-tag-brace">
                      {c.ref_sing_path ? "SING READY" : "NO SING"}
                    </span>
                  </span>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <div
        className="hk-divider-diamond hk-container-narrow"
        style={{ marginBlock: "var(--space-xl)" }}
      >
        <span className="hk-divider-diamond__line" />
      </div>

      {/* 下半区：角色引导建档表单 */}
      <div className="hk-container-narrow hk-reveal">
        <div className="hk-section-head">
          <h2 className="hk-section-title">REGISTER</h2>
          <span className="hk-section-sub">零样本声纹建档 · 支持上传与现场录制</span>
        </div>

        <form className="hk-form-stack" onSubmit={(e) => void handleSubmit(e)}>
          <div className="hk-form-row-2">
            <label className="hk-field">
              <span className="hk-field__label">角色名称 *</span>
              <input
                type="text"
                className="hk-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：星野"
                required
              />
            </label>

            <div className="hk-field">
              <span className="hk-field__label">预留虚拟形象类型</span>
              <HkSelect
                value={avatarType}
                ariaLabel="预留虚拟形象类型"
                options={[
                  { value: "none", label: "暂不挂载 (none)" },
                  { value: "live2d", label: "Live2D (PixiJS)" },
                  { value: "vrm", label: "3D VRM (Three.js)" },
                ]}
                onChange={(v) => setAvatarType(v as "none" | "live2d" | "vrm")}
              />
            </div>
          </div>

          <label className="hk-field">
            <span className="hk-field__label">角色简介</span>
            <input
              type="text"
              className="hk-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="音色风格、人设说明或声线特征（可选）"
            />
          </label>

          {/* 采集方式极简下划线切换 */}
          <div className="hk-tabs" style={{ marginBottom: 0 }}>
            <button
              type="button"
              className={`hk-tab ${mode === "upload" ? "hk-tab--active" : ""}`}
              disabled={recordingTarget !== null}
              onClick={() => handleModeSwitch("upload")}
            >
              方式一：自行上传语音文件
            </button>
            <button
              type="button"
              className={`hk-tab ${mode === "record" ? "hk-tab--active" : ""}`}
              disabled={recordingTarget !== null}
              onClick={() => handleModeSwitch("record")}
            >
              方式二：现场麦克风录制
            </button>
          </div>

          {mode === "upload" ? (
            <div className="hk-form-stack">
              <div className="hk-form-block">
                <h3 className="hk-form-block__title">
                  01 · 朗读参考音频（建议 5~15 秒，用于 CosyVoice3 对话克隆）
                </h3>
                <div className="hk-form-stack" style={{ gap: "1rem" }}>
                  <div className="hk-field">
                    <span className="hk-field__label">朗读音频文件 (.wav / .flac / .mp3)</span>
                    <AudioFilePicker
                      selectedFileName={speechClip?.filename}
                      placeholder="拖拽朗读参考音频至此，或点击右侧选择"
                      onSelect={(file) => void handleFileSelect("speech", file)}
                    />
                  </div>
                  <label className="hk-field">
                    <span className="hk-field__label">
                      朗读音频对应文本（可选，填写后零样本克隆更稳）
                    </span>
                    <input
                      type="text"
                      className="hk-input"
                      value={speechText}
                      onChange={(e) => setSpeechText(e.target.value)}
                      placeholder="填写该段朗读音频对应的文字内容（留空则使用默认提示）"
                    />
                  </label>
                </div>
                {speechClip ? (
                  <div className="hk-clip-bar">
                    <audio controls src={speechClip.url} />
                    <span className="hk-secondary" style={{ fontSize: "var(--text-xs)" }}>
                      {speechClip.filename}
                      {speechClip.durationSec > 0 ? ` · ${speechClip.durationSec.toFixed(1)}s` : ""}{" "}
                      · SHA256 {speechClip.sha256.slice(0, 12)}
                    </span>
                  </div>
                ) : null}
              </div>

              <div className="hk-form-block">
                <h3 className="hk-form-block__title">
                  02 · 清唱参考音频（建议 5~15 秒无伴奏，用于 Seed-VC 翻唱克隆）
                </h3>
                <div className="hk-field">
                  <span className="hk-field__label">清唱音频文件 (.wav / .flac / .mp3)</span>
                  <AudioFilePicker
                    selectedFileName={singClip?.filename}
                    placeholder="拖拽无伴奏清唱音频至此，或点击右侧选择"
                    onSelect={(file) => void handleFileSelect("sing", file)}
                  />
                </div>
                {singClip ? (
                  <div className="hk-clip-bar">
                    <audio controls src={singClip.url} />
                    <span className="hk-secondary" style={{ fontSize: "var(--text-xs)" }}>
                      {singClip.filename}
                      {singClip.durationSec > 0 ? ` · ${singClip.durationSec.toFixed(1)}s` : ""} ·
                      SHA256 {singClip.sha256.slice(0, 12)}
                    </span>
                  </div>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="hk-form-stack">
              <div className="hk-form-block">
                <h3 className="hk-form-block__title">
                  01 · 朗读录制（建议 ~15 秒，用于 TTS 对话克隆）
                </h3>
                <p className="hk-quote">“{PROMPT_TEXT}”</p>
                <div style={{ display: "flex", gap: "0.75rem" }}>
                  {recordingTarget === "speech" ? (
                    <button
                      type="button"
                      className="hk-btn hk-btn--primary"
                      onClick={() => void stopRecording()}
                    >
                      停止朗读录制
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="hk-btn"
                      disabled={recordingTarget !== null}
                      onClick={() => void startRecording("speech")}
                    >
                      开始朗读录制
                    </button>
                  )}
                </div>
                {speechClip ? (
                  <div className="hk-clip-bar">
                    <audio controls src={speechClip.url} />
                    <span className="hk-secondary" style={{ fontSize: "var(--text-xs)" }}>
                      时长 {speechClip.durationSec.toFixed(1)}s · SHA256{" "}
                      {speechClip.sha256.slice(0, 12)}
                    </span>
                  </div>
                ) : null}
              </div>

              <div className="hk-form-block">
                <h3 className="hk-form-block__title">
                  02 · 清唱录制（建议 ~10 秒，用于 Seed-VC 歌唱音域捕捉）
                </h3>
                <p className="hk-secondary" style={{ fontSize: "var(--text-xs)", marginTop: 0 }}>
                  请清唱一段副歌（无伴奏），尽量覆盖自然高音与转音。
                </p>
                <div style={{ display: "flex", gap: "0.75rem" }}>
                  {recordingTarget === "sing" ? (
                    <button
                      type="button"
                      className="hk-btn hk-btn--primary"
                      onClick={() => void stopRecording()}
                    >
                      停止清唱录制
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="hk-btn"
                      disabled={recordingTarget !== null}
                      onClick={() => void startRecording("sing")}
                    >
                      开始清唱录制
                    </button>
                  )}
                </div>
                {singClip ? (
                  <div className="hk-clip-bar">
                    <audio controls src={singClip.url} />
                    <span className="hk-secondary" style={{ fontSize: "var(--text-xs)" }}>
                      时长 {singClip.durationSec.toFixed(1)}s · SHA256{" "}
                      {singClip.sha256.slice(0, 12)}
                    </span>
                  </div>
                ) : null}
              </div>

              {recordingTarget ? (
                <div>
                  <div
                    className="hk-secondary"
                    style={{ fontSize: "var(--text-xs)", marginBottom: "0.35rem" }}
                  >
                    INPUT LEVEL ·{" "}
                    <span className="hk-num" style={{ color: "var(--color-ink-primary)" }}>
                      {Math.round(level * 100)}%
                    </span>
                  </div>
                  <div className="hk-meter">
                    <div className="hk-meter__fill" style={{ width: `${Math.round(level * 100)}%` }} />
                  </div>
                </div>
              ) : null}
            </div>
          )}

          <div className="hk-form-submit-row">
            <button
              type="submit"
              className="hk-btn-submit"
              disabled={submitting || recordingTarget !== null}
            >
              {submitting ? "正在上传并建档..." : "提交并注册角色"}
            </button>
          </div>
        </form>

        {statusMsg ? <div className="hk-status-note">{statusMsg}</div> : null}
      </div>
    </section>
  );
}
