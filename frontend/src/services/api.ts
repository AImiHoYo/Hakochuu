export interface UploadCheckResponse {
  existing: string[];
  missing: string[];
  paths: Record<string, string>;
}

export interface UploadAssetResponse {
  sha256: string;
  path: string;
  size: number;
  kind: string;
}

export interface CharacterProfile {
  id: string;
  name: string;
  description: string | null;
  ref_speech_path: string | null;
  ref_speech_text: string | null;
  ref_sing_path: string | null;
  avatar_type: "none" | "live2d" | "vrm" | "webrtc_video";
  avatar_asset_url: string | null;
  lip_sync_mode: "rms" | "viseme";
  created_at: string;
}

export async function uploadCheck(
  hashes: string[],
  kind: "speech" | "sing" | "song" | "avatar"
): Promise<UploadCheckResponse> {
  const resp = await fetch("/v1/assets/upload-check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hashes, kind }),
  });
  if (!resp.ok) {
    throw new Error(`upload-check failed: ${resp.status}`);
  }
  return resp.json() as Promise<UploadCheckResponse>;
}

export async function uploadAssetFile(
  blob: Blob,
  filename: string,
  sha256: string,
  kind: "speech" | "sing" | "song" | "avatar"
): Promise<UploadAssetResponse> {
  const form = new FormData();
  form.append("file", blob, filename);
  form.append("sha256", sha256);
  form.append("kind", kind);

  const resp = await fetch("/v1/assets/upload", {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    throw new Error(`upload failed: ${resp.status}`);
  }
  return resp.json() as Promise<UploadAssetResponse>;
}

export async function registerCharacter(payload: {
  name: string;
  description?: string;
  ref_speech_path?: string;
  ref_speech_text?: string;
  ref_sing_path?: string;
  avatar_type?: "none" | "live2d" | "vrm" | "webrtc_video";
}): Promise<CharacterProfile> {
  const resp = await fetch("/v1/characters/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    throw new Error(`register failed: ${resp.status}`);
  }
  return resp.json() as Promise<CharacterProfile>;
}

export async function listCharacters(): Promise<CharacterProfile[]> {
  const resp = await fetch("/v1/characters");
  if (!resp.ok) {
    throw new Error(`list characters failed: ${resp.status}`);
  }
  return resp.json() as Promise<CharacterProfile[]>;
}

export async function deleteCharacter(charId: string): Promise<void> {
  const resp = await fetch(`/v1/characters/${encodeURIComponent(charId)}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    throw new Error(`delete character failed: ${resp.status}`);
  }
}

export interface SongItem {
  id: string;
  title: string;
  artist: string | null;
  duration_ms: number;
}

export async function listSongs(): Promise<SongItem[]> {
  const resp = await fetch("/v1/songs");
  if (!resp.ok) {
    throw new Error(`list songs failed: ${resp.status}`);
  }
  return resp.json() as Promise<SongItem[]>;
}

export interface SongSeparationJobItem {
  id: string;
  song_id: string;
  title: string;
  artist: string | null;
  status: "queued" | "running" | "completed" | "failed";
  progress: number;
  stage: string;
  duration_ms: number | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export async function getNextSongId(): Promise<string> {
  const resp = await fetch("/v1/songs/next-id");
  if (!resp.ok) {
    throw new Error(`get next song id failed: ${resp.status}`);
  }
  const data = (await resp.json()) as { next_song_id: string };
  return data.next_song_id;
}

export async function createSongSeparationJob(payload: {
  file: File;
  songId?: string;
  title?: string;
  artist?: string;
}): Promise<SongSeparationJobItem> {
  const form = new FormData();
  if (payload.songId?.trim()) {
    form.append("song_id", payload.songId.trim());
  }
  if (payload.title?.trim()) {
    form.append("title", payload.title.trim());
  }
  if (payload.artist?.trim()) {
    form.append("artist", payload.artist.trim());
  }
  form.append("file", payload.file);

  const resp = await fetch("/v1/songs/separate/jobs", {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    const errText = await resp.text();
    throw new Error(errText || `create separation job failed: ${resp.status}`);
  }
  return resp.json() as Promise<SongSeparationJobItem>;
}

export async function listSongSeparationJobs(): Promise<SongSeparationJobItem[]> {
  const resp = await fetch("/v1/songs/separate/jobs");
  if (!resp.ok) {
    throw new Error(`list separation jobs failed: ${resp.status}`);
  }
  return resp.json() as Promise<SongSeparationJobItem[]>;
}

export function subscribeSongSeparationStream(
  jobId: string,
  onUpdate: (job: SongSeparationJobItem) => void
): EventSource {
  const es = new EventSource(`/v1/songs/separate/jobs/${encodeURIComponent(jobId)}/stream`);
  es.onmessage = (event: MessageEvent<string>) => {
    try {
      const data = JSON.parse(event.data) as SongSeparationJobItem;
      onUpdate(data);
      if (data.status === "completed" || data.status === "failed") {
        es.close();
      }
    } catch {
      /* 忽略非 JSON 心跳 */
    }
  };
  es.onerror = () => {
    es.close();
  };
  return es;
}

export interface FinetuneJobItem {
  id: string;
  character_id: string;
  target_engine: "qwen3_tts" | "gpt_sovits" | "seed_vc";
  status: string;
  progress: number;
  error_message: string | null;
}

export async function createFinetuneJob(
  characterId: string,
  targetEngine: "qwen3_tts" | "gpt_sovits" | "seed_vc"
): Promise<FinetuneJobItem> {
  const resp = await fetch("/v1/finetune/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ character_id: characterId, target_engine: targetEngine }),
  });
  if (!resp.ok) {
    throw new Error(`create finetune job failed: ${resp.status}`);
  }
  return resp.json() as Promise<FinetuneJobItem>;
}

export async function listFinetuneJobs(): Promise<FinetuneJobItem[]> {
  const resp = await fetch("/v1/finetune/jobs");
  if (!resp.ok) {
    throw new Error(`list finetune jobs failed: ${resp.status}`);
  }
  return resp.json() as Promise<FinetuneJobItem[]>;
}

export function subscribeFinetuneStream(
  jobId: string,
  onUpdate: (job: FinetuneJobItem) => void
): EventSource {
  const es = new EventSource(`/v1/finetune/jobs/${encodeURIComponent(jobId)}/stream`);
  es.onmessage = (event: MessageEvent<string>) => {
    try {
      const data = JSON.parse(event.data) as FinetuneJobItem;
      onUpdate(data);
      if (data.status === "completed" || data.status === "failed") {
        es.close();
      }
    } catch {
      /* 忽略非 JSON 心跳 */
    }
  };
  es.onerror = () => {
    es.close();
  };
  return es;
}

export interface LlmParamOption {
  value: string;
  label: string;
  keywords?: string;
  style?: string;
}

export interface LlmCustomParam {
  key: string;
  label: string;
  type: "select" | "text" | "slider";
  default: string | number;
  description?: string;
  placeholder?: string;
  min?: number;
  max?: number;
  step?: number;
  options?: LlmParamOption[];
}

export interface LlmModelConfig {
  id: string;
  display_name: string;
  hf_repo?: string;
  base_model?: string;
  character_binding?: {
    shares_global_characters: boolean;
    supports_emotion_tags: boolean;
  };
  custom_params: LlmCustomParam[];
}

export async function listLlmModels(): Promise<LlmModelConfig[]> {
  const resp = await fetch("/v1/models/llms");
  if (!resp.ok) {
    throw new Error(`list llm models failed: ${resp.status}`);
  }
  return resp.json() as Promise<LlmModelConfig[]>;
}

export type LogServiceId = "gateway" | "vllm" | "asr" | "tts" | "vc";

export interface LogServiceInfo {
  service: LogServiceId;
  label: string;
  port: number;
  log_file: string;
  available: boolean;
  size_bytes: number;
  updated_at: string | null;
  healthy: boolean;
}

export interface LogQueryResponse {
  service: LogServiceId;
  label: string;
  port: number;
  available: boolean;
  size_bytes: number;
  updated_at: string | null;
  lines: string[];
  returned: number;
}

export async function listLogServices(): Promise<LogServiceInfo[]> {
  const resp = await fetch("/v1/logs/services");
  if (!resp.ok) {
    throw new Error(`list log services failed: ${resp.status}`);
  }
  const data = (await resp.json()) as { services: LogServiceInfo[] };
  return data.services;
}

export async function fetchServiceLogs(
  service: LogServiceId,
  params: { lines?: number; level?: string; q?: string } = {}
): Promise<LogQueryResponse> {
  const qs = new URLSearchParams();
  if (params.lines) qs.set("lines", String(params.lines));
  if (params.level) qs.set("level", params.level);
  if (params.q) qs.set("q", params.q);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const resp = await fetch(`/v1/logs/${encodeURIComponent(service)}${suffix}`);
  if (!resp.ok) {
    throw new Error(`fetch logs failed: ${resp.status}`);
  }
  return resp.json() as Promise<LogQueryResponse>;
}

export function subscribeServiceLogStream(
  service: LogServiceId,
  params: { lines?: number; level?: string; q?: string },
  onLine: (line: string) => void,
  onError?: () => void
): EventSource {
  const qs = new URLSearchParams();
  if (params.lines !== undefined) qs.set("lines", String(params.lines));
  if (params.level) qs.set("level", params.level);
  if (params.q) qs.set("q", params.q);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const es = new EventSource(`/v1/logs/${encodeURIComponent(service)}/stream${suffix}`);
  es.onmessage = (event: MessageEvent<string>) => {
    try {
      const data = JSON.parse(event.data) as { line?: string };
      if (typeof data.line === "string") {
        onLine(data.line);
      }
    } catch {
      /* 忽略非 JSON 心跳 */
    }
  };
  es.onerror = () => {
    onError?.();
  };
  return es;
}
