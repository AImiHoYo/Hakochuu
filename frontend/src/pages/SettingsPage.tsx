import { useCallback, useEffect, useRef, useState } from "react";
import { AudioFilePicker } from "../components/common/AudioFilePicker";
import { HkSelect } from "../components/common/HkSelect";
import {
  CharacterProfile,
  FinetuneJobItem,
  SongItem,
  SongSeparationJobItem,
  createFinetuneJob,
  createSongSeparationJob,
  getNextSongId,
  listCharacters,
  listFinetuneJobs,
  listSongSeparationJobs,
  listSongs,
  subscribeFinetuneStream,
  subscribeSongSeparationStream,
} from "../services/api";

interface SystemCapabilities {
  protocol_version: string;
  deploy_mode: string;
  mock_engines: boolean;
  supported_codecs: string[];
}

interface GpuStatusResp {
  active_mode: string;
  vram_limit_mb: number;
  estimated_used_mb: number;
  tts_location: string;
  vc_location: string;
}

function parseSongFilename(filename: string): { artist: string; title: string } {
  const stem = filename.replace(/\.[^.]+$/, "").trim();
  if (!stem) {
    return { artist: "", title: "" };
  }
  const trackPrefixRe = /^\d{1,3}[\.\s、_-]+\s*/;
  for (const sep of [" - ", " – ", " — ", " － "]) {
    const idx = stem.indexOf(sep);
    if (idx > 0) {
      const artist = stem.slice(0, idx).replace(trackPrefixRe, "").trim();
      const title = stem.slice(idx + sep.length).trim();
      if (title) {
        return { artist, title };
      }
    }
  }
  return { artist: "", title: stem.replace(trackPrefixRe, "").trim() || stem };
}

function computeNextNumericSongId(
  songs: SongItem[],
  sepJobs: SongSeparationJobItem[]
): string {
  let maxNum = 0;
  for (const s of songs) {
    if (/^\d+$/.test(s.id)) {
      maxNum = Math.max(maxNum, Number(s.id));
    }
  }
  for (const j of sepJobs) {
    if (j.status !== "failed" && /^\d+$/.test(j.song_id)) {
      maxNum = Math.max(maxNum, Number(j.song_id));
    }
  }
  return String(maxNum + 1);
}

export function SettingsPage() {
  const streamRef = useRef<EventSource | null>(null);
  const sepStreamsRef = useRef<Map<string, EventSource>>(new Map());

  const [capabilities, setCapabilities] = useState<SystemCapabilities | null>(null);
  const [gpuStatus, setGpuStatus] = useState<GpuStatusResp | null>(null);
  const [characters, setCharacters] = useState<CharacterProfile[]>([]);
  const [jobs, setJobs] = useState<FinetuneJobItem[]>([]);
  const [songs, setSongs] = useState<SongItem[]>([]);
  const [sepJobs, setSepJobs] = useState<SongSeparationJobItem[]>([]);

  const [selectedCharId, setSelectedCharId] = useState("");
  const [targetEngine, setTargetEngine] = useState<"qwen3_tts" | "gpt_sovits" | "seed_vc">(
    "qwen3_tts"
  );
  const [activeJob, setActiveJob] = useState<FinetuneJobItem | null>(null);
  const [statusMsg, setStatusMsg] = useState("");

  // 曲库异步分离状态（song_id 按 1, 2, 3... 自动递增）
  const [songId, setSongId] = useState("1");
  const [songIdDirty, setSongIdDirty] = useState(false);
  const [songTitle, setSongTitle] = useState("");
  const [songArtist, setSongArtist] = useState("");
  const [songFile, setSongFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [songMsg, setSongMsg] = useState("");

  const refreshSongsAndNextId = useCallback(
    async (forceSyncSongId = false) => {
      try {
        const [songList, nextId] = await Promise.all([listSongs(), getNextSongId()]);
        setSongs(songList);
        if (forceSyncSongId || !songIdDirty) {
          setSongId(nextId);
        }
      } catch {
        /* 后端未启动时忽略 */
      }
    },
    [songIdDirty]
  );

  const attachSepStream = useCallback(
    (jobId: string) => {
      if (sepStreamsRef.current.has(jobId)) return;
      const es = subscribeSongSeparationStream(jobId, (updated) => {
        setSepJobs((prev) => {
          const exists = prev.some((j) => j.id === updated.id);
          return exists
            ? prev.map((j) => (j.id === updated.id ? updated : j))
            : [updated, ...prev];
        });
        if (updated.status === "completed") {
          sepStreamsRef.current.delete(jobId);
          setSongMsg(`分离完成并已入库：${updated.title} (#${updated.song_id})`);
          void refreshSongsAndNextId();
        } else if (updated.status === "failed") {
          sepStreamsRef.current.delete(jobId);
          setSongMsg(`分离失败 (#${updated.song_id}): ${updated.error_message || "未知错误"}`);
          void refreshSongsAndNextId();
        }
      });
      sepStreamsRef.current.set(jobId, es);
    },
    [refreshSongsAndNextId]
  );

  const loadData = useCallback(() => {
    fetch("/v1/system/capabilities")
      .then((r) => r.json())
      .then((d: SystemCapabilities) => setCapabilities(d))
      .catch(() => {});

    fetch("/v1/system/gpu")
      .then((r) => r.json())
      .then((d: GpuStatusResp) => setGpuStatus(d))
      .catch(() => {});

    void refreshSongsAndNextId(true);

    listSongSeparationJobs()
      .then((list) => {
        setSepJobs(list);
        for (const item of list) {
          if (item.status === "queued" || item.status === "running") {
            attachSepStream(item.id);
          }
        }
      })
      .catch(() => {});

    listCharacters()
      .then((chars) => {
        setCharacters(chars);
        if (chars.length > 0 && !selectedCharId) {
          setSelectedCharId(chars[0].id);
        }
      })
      .catch(() => {});

    listFinetuneJobs()
      .then(setJobs)
      .catch(() => {});
  }, [attachSepStream, refreshSongsAndNextId, selectedCharId]);

  useEffect(() => {
    loadData();
    return () => {
      streamRef.current?.close();
      for (const es of sepStreamsRef.current.values()) {
        es.close();
      }
      sepStreamsRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSongFileChange = (file: File | null) => {
    setSongFile(file);
    if (!file) return;
    const { artist, title } = parseSongFilename(file.name);
    setSongTitle(title);
    setSongArtist(artist);
    if (!songIdDirty) {
      setSongId(computeNextNumericSongId(songs, sepJobs));
    }
  };

  const handleStartFinetune = async () => {
    if (!selectedCharId) {
      setStatusMsg("请先在 PERSONA 页面注册至少一个角色");
      return;
    }
    try {
      const job = await createFinetuneJob(selectedCharId, targetEngine);
      setActiveJob(job);
      setStatusMsg(`已投递微调任务 ${job.id}，正在监听 SSE 进度...`);

      streamRef.current?.close();
      streamRef.current = subscribeFinetuneStream(job.id, (updated) => {
        setActiveJob(updated);
        if (updated.status === "completed") {
          setStatusMsg(`微调任务 ${updated.id} 已完成，权重可热挂载。`);
          loadData();
        }
      });
    } catch (err) {
      setStatusMsg(`创建微调任务失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleSeparateSong = async () => {
    if (!songFile) {
      setSongMsg("请先选择原曲音频文件");
      return;
    }
    setUploading(true);
    setSongMsg("正在上传原曲并加入后台分离队列...");
    try {
      const created = await createSongSeparationJob({
        file: songFile,
        songId: songId.trim() || undefined,
        title: songTitle.trim() || undefined,
        artist: songArtist.trim() || undefined,
      });

      const nextJobs = [created, ...sepJobs.filter((j) => j.id !== created.id)];
      setSepJobs(nextJobs);
      attachSepStream(created.id);
      setSongMsg(
        `已加入后台分离队列：#${created.song_id} ${created.title}（可随时展开左下角 LOG 查看实时进度）`
      );

      setSongFile(null);
      setSongTitle("");
      setSongArtist("");
      setSongIdDirty(false);
      setSongId(computeNextNumericSongId(songs, nextJobs));
    } catch (err) {
      setSongMsg(`提交分离任务失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(false);
    }
  };

  const vramPercent = gpuStatus
    ? Math.min(100, Math.round((gpuStatus.estimated_used_mb / Math.max(1, gpuStatus.vram_limit_mb)) * 100))
    : 0;

  return (
    <section className="hk-container-wide">
      {/* 居中对称抬头 */}
      <header className="hk-page-header">
        <div className="hk-page-header__motif" aria-hidden="true">
          <svg width="26" height="18" viewBox="0 0 26 18" fill="none" stroke="currentColor" strokeWidth="1">
            <path d="M13 2C14.8 6 17.5 8.5 22 9C17.5 9.5 14.8 12 13 16C11.2 12 8.5 9.5 4 9C8.5 8.5 11.2 6 13 2Z" />
          </svg>
        </div>
        <h1 className="hk-page-header__title">ATELIER</h1>
        <p className="hk-page-header__subtitle">Repertoire · BS-RoFormer Separation · Fine-Tuning</p>
      </header>

      {/* 1. 系统节点能力与单卡显存槽位 */}
      <div className="hk-container-narrow hk-reveal">
        <div className="hk-divider-diamond">
          <span className="hk-divider-diamond__line" />
        </div>

        <div className="hk-metrics-strip">
          <div className="hk-telemetry-item">
            <span className="hk-telemetry-item__label">DEPLOY MODE</span>
            <span className="hk-telemetry-item__value">
              {capabilities ? capabilities.deploy_mode : "未连接"}
            </span>
          </div>
          <div className="hk-telemetry-item">
            <span className="hk-telemetry-item__label">MOCK ENGINES</span>
            <span className="hk-telemetry-item__value">
              {capabilities ? String(capabilities.mock_engines) : "—"}
            </span>
          </div>
          <div className="hk-telemetry-item">
            <span className="hk-telemetry-item__label">CODECS</span>
            <span className="hk-telemetry-item__value hk-serif">
              {capabilities ? capabilities.supported_codecs.join(" · ") : "—"}
            </span>
          </div>
          <div className="hk-telemetry-item">
            <span className="hk-telemetry-item__label">TTS / VC SLOTS</span>
            <span className="hk-telemetry-item__value hk-serif">
              {gpuStatus ? `${gpuStatus.tts_location} / ${gpuStatus.vc_location}` : "—"}
            </span>
          </div>
        </div>

        {gpuStatus ? (
          <div style={{ paddingBottom: "1.25rem" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "var(--text-xs)",
                color: "var(--color-ink-secondary)",
                marginBottom: "0.4rem",
              }}
            >
              <span>GPU 显存预算 · {gpuStatus.active_mode}</span>
              <span className="hk-num" style={{ color: "var(--color-ink-primary)" }}>
                {gpuStatus.estimated_used_mb} / {gpuStatus.vram_limit_mb} MB ({vramPercent}%)
              </span>
            </div>
            <div className="hk-meter">
              <div className="hk-meter__fill" style={{ width: `${vramPercent}%` }} />
            </div>
          </div>
        ) : null}

        <div className="hk-divider-diamond">
          <span className="hk-divider-diamond__line" />
        </div>
      </div>

      {/* 2. 已分离曲库画廊 */}
      <div style={{ marginTop: "var(--space-xl)" }}>
        <div className="hk-section-head">
          <h2 className="hk-section-title">REPERTOIRE</h2>
          <span className="hk-section-sub hk-num">{songs.length} TRACKS</span>
        </div>

        {songs.length === 0 ? (
          <p className="hk-secondary" style={{ textAlign: "center", paddingBlock: "2rem" }}>
            曲库暂无已分离歌曲，可在下方提交 BS-RoFormer 异步分离任务。
          </p>
        ) : (
          <div className="hk-release-grid">
            {songs.map((s, idx) => (
              <div key={s.id} className={`hk-release-card hk-reveal hk-reveal--delay-${(idx % 3) + 1}`}>
                <div className="hk-song-art">
                  <div className="hk-song-art__no">#{s.id}</div>
                  <div className="hk-tag-brace">INSTRUMENTAL + VOCAL</div>
                  <div className="hk-song-art__dur">{(s.duration_ms / 1000).toFixed(1)}s</div>
                </div>
                <div className="hk-release-card__type">{s.artist || "Unknown Artist"}</div>
                <div className="hk-release-card__title">{s.title}</div>
              </div>
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

      {/* 3. BS-RoFormer 异步人声/伴奏分离表单与队列 */}
      <div className="hk-container-narrow hk-reveal">
        <div className="hk-section-head">
          <h2 className="hk-section-title">SEPARATION</h2>
          <span className="hk-section-sub">BS-RoFormer 异步队列 · 自动识别歌手与歌名</span>
        </div>

        <div className="hk-form-stack">
          <div className="hk-field">
            <span className="hk-field__label">
              原曲音频文件 (.mp3 / .wav / .flac，自动识别「歌手 - 歌名」)
            </span>
            <AudioFilePicker
              selectedFileName={songFile?.name}
              placeholder="拖拽原曲音频至此，或点击右侧选择"
              disabled={uploading}
              onSelect={(file) => handleSongFileChange(file)}
            />
          </div>

          <div className="hk-form-row-2">
            <label className="hk-field">
              <span className="hk-field__label">歌曲编号 (song_id，按 1, 2, 3... 自动顺延)</span>
              <input
                type="text"
                className="hk-input"
                value={songId}
                onChange={(e) => {
                  setSongId(e.target.value);
                  setSongIdDirty(true);
                }}
              />
            </label>

            <label className="hk-field">
              <span className="hk-field__label">原唱歌手（可选）</span>
              <input
                type="text"
                className="hk-input"
                value={songArtist}
                placeholder="从文件名自动提取"
                onChange={(e) => setSongArtist(e.target.value)}
              />
            </label>
          </div>

          <label className="hk-field">
            <span className="hk-field__label">歌曲名称</span>
            <input
              type="text"
              className="hk-input"
              value={songTitle}
              placeholder="选择文件后自动识别，也可手动修改"
              onChange={(e) => setSongTitle(e.target.value)}
            />
          </label>

          <div className="hk-form-submit-row">
            <button
              type="button"
              className="hk-btn-submit"
              disabled={uploading || !songFile}
              onClick={() => void handleSeparateSong()}
            >
              {uploading ? "正在上传..." : "加入后台分离队列"}
            </button>
          </div>
        </div>

        {songMsg ? <div className="hk-status-note">{songMsg}</div> : null}

        {/* 异步分离进度队列 */}
        {sepJobs.length > 0 ? (
          <div className="hk-queue-list">
            <div className="hk-divider-diamond">
              <span className="hk-divider-diamond__line" />
            </div>
            {sepJobs.map((j) => (
              <div key={j.id} className="hk-queue-row-wrap">
                <div className="hk-news-row hk-motion-fade">
                  <div className="hk-queue-id">
                    <span className="hk-queue-id__main">#{j.song_id}</span>
                    <span className="hk-queue-id__sub">{j.progress}%</span>
                  </div>
                  <div>
                    <div style={{ marginBottom: "0.35rem" }}>
                      {j.title}
                      {j.artist ? <span className="hk-secondary"> — {j.artist}</span> : null}
                      <span
                        className="hk-secondary"
                        style={{ fontSize: "var(--text-xs)", marginLeft: "0.65rem" }}
                      >
                        {j.stage}
                      </span>
                    </div>
                    <div className="hk-meter">
                      <div
                        className={`hk-meter__fill ${
                          j.status === "failed" ? "hk-meter__fill--error" : ""
                        }`}
                        style={{ width: `${j.progress}%` }}
                      />
                    </div>
                    {j.error_message ? (
                      <div
                        style={{ color: "#9e473c", fontSize: "var(--text-xs)", marginTop: "0.35rem" }}
                      >
                        {j.error_message}
                      </div>
                    ) : null}
                  </div>
                  <div className="hk-tag-brace">{j.status.toUpperCase()}</div>
                </div>
                <div className="hk-divider-diamond">
                  <span className="hk-divider-diamond__line" />
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      <div
        className="hk-divider-diamond hk-container-narrow"
        style={{ marginBlock: "var(--space-xl)" }}
      >
        <span className="hk-divider-diamond__line" />
      </div>

      {/* 4. 异步微调队列 */}
      <div className="hk-container-narrow hk-reveal">
        <div className="hk-section-head">
          <h2 className="hk-section-title">FINE-TUNING</h2>
          <span className="hk-section-sub">异步 LoRA 与音色精修队列</span>
        </div>

        <div className="hk-form-stack">
          <div className="hk-form-row-2">
            <div className="hk-field">
              <span className="hk-field__label">选择角色档案</span>
              <HkSelect
                value={selectedCharId}
                ariaLabel="选择角色档案"
                options={
                  characters.length === 0
                    ? [{ value: "", label: "暂无角色" }]
                    : characters.map((c) => ({
                        value: c.id,
                        label: `${c.name} (${c.id})`,
                      }))
                }
                onChange={setSelectedCharId}
              />
            </div>

            <div className="hk-field">
              <span className="hk-field__label">微调目标引擎</span>
              <HkSelect
                value={targetEngine}
                ariaLabel="微调目标引擎"
                options={[
                  { value: "qwen3_tts", label: "Qwen3-TTS LoRA（对话音色精修）" },
                  { value: "gpt_sovits", label: "GPT-SoVITS（少样本深度克隆）" },
                  { value: "seed_vc", label: "Seed-VC（歌唱音域与咬字精修）" },
                ]}
                onChange={(v) => setTargetEngine(v as "qwen3_tts" | "gpt_sovits" | "seed_vc")}
              />
            </div>
          </div>

          <div className="hk-form-submit-row">
            <button
              type="button"
              className="hk-btn-submit"
              onClick={() => void handleStartFinetune()}
            >
              提交异步微调任务
            </button>
          </div>
        </div>

        {activeJob ? (
          <div style={{ marginTop: "1.25rem" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "var(--text-xs)",
                color: "var(--color-ink-secondary)",
                marginBottom: "0.4rem",
              }}
            >
              <span>
                当前任务 <strong className="hk-num">{activeJob.id}</strong> · {activeJob.status}
              </span>
              <span className="hk-num" style={{ color: "var(--color-ink-primary)" }}>
                {activeJob.progress}%
              </span>
            </div>
            <div className="hk-meter">
              <div className="hk-meter__fill" style={{ width: `${activeJob.progress}%` }} />
            </div>
          </div>
        ) : null}

        {statusMsg ? <div className="hk-status-note">{statusMsg}</div> : null}

        {/* 历史微调任务列表 */}
        <div className="hk-queue-list" style={{ marginTop: "var(--space-lg)" }}>
          <div className="hk-section-head" style={{ marginBottom: "0.75rem" }}>
            <span className="hk-serif" style={{ letterSpacing: "0.14em" }}>
              JOB HISTORY
            </span>
            <span className="hk-section-sub hk-num">{jobs.length} RECORDS</span>
          </div>

          {jobs.length === 0 ? (
            <p className="hk-secondary" style={{ fontSize: "var(--text-sm)" }}>
              暂无微调任务记录。
            </p>
          ) : (
            <>
              <div className="hk-divider-diamond">
                <span className="hk-divider-diamond__line" />
              </div>
              {jobs.map((j) => (
                <div key={j.id} className="hk-queue-row-wrap">
                  <div className="hk-news-row hk-motion-fade">
                    <div className="hk-queue-id">
                      <span className="hk-queue-id__main">{j.progress}%</span>
                      <span className="hk-queue-id__sub">{j.target_engine}</span>
                    </div>
                    <div>
                      <span className="hk-num">{j.id}</span>
                      <span className="hk-secondary" style={{ marginLeft: "0.75rem" }}>
                        角色 {j.character_id}
                      </span>
                    </div>
                    <div className="hk-tag-brace">{j.status.toUpperCase()}</div>
                  </div>
                  <div className="hk-divider-diamond">
                    <span className="hk-divider-diamond__line" />
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
