/**
 * 浏览器端 Web Audio API 双轨混音器：
 * 1. 伴奏轨：通过 /v1/songs/{id}/instrumental 拉取（利用浏览器 HTTP ETag 强缓存 + 内存 AudioBuffer 缓存）
 * 2. 人声轨：接收服务端下发的 0x03 (VC_VOCAL_DOWN) 纯人声干声帧，按 PTS 毫秒时间戳严格对齐伴奏时间轴
 */
export class DualTrackMixer {
  private audioCtx: AudioContext | null = null;
  private instrumentalGain: GainNode | null = null;
  private vocalGain: GainNode | null = null;

  private bufferCache = new Map<string, AudioBuffer>();
  private instrumentalSource: AudioBufferSourceNode | null = null;
  private scheduledVocalSources = new Set<AudioBufferSourceNode>();

  private activeGenId = 1;
  private songOriginAudioTime = 0;
  private singingActive = false;
  private sampleRate: number;

  constructor(sampleRate = 24000) {
    this.sampleRate = sampleRate;
  }

  get isSinging(): boolean {
    return this.singingActive;
  }

  async ensureContext(existingContext?: AudioContext): Promise<AudioContext> {
    if (!this.audioCtx) {
      this.audioCtx =
        existingContext ??
        new AudioContext({ sampleRate: this.sampleRate, latencyHint: "interactive" });
      this.instrumentalGain = this.audioCtx.createGain();
      this.vocalGain = this.audioCtx.createGain();
      this.instrumentalGain.gain.value = 0.7;
      this.vocalGain.gain.value = 1.0;
      this.instrumentalGain.connect(this.audioCtx.destination);
      this.vocalGain.connect(this.audioCtx.destination);
    }
    if (this.audioCtx.state === "suspended") {
      await this.audioCtx.resume();
    }
    return this.audioCtx;
  }

  setInstrumentalVolume(volume: number): void {
    if (this.instrumentalGain && this.audioCtx) {
      this.instrumentalGain.gain.setTargetAtTime(
        Math.max(0, Math.min(1.5, volume)),
        this.audioCtx.currentTime,
        0.015
      );
    }
  }

  setVocalVolume(volume: number): void {
    if (this.vocalGain && this.audioCtx) {
      this.vocalGain.gain.setTargetAtTime(
        Math.max(0, Math.min(1.5, volume)),
        this.audioCtx.currentTime,
        0.015
      );
    }
  }

  async loadInstrumental(songId: string): Promise<AudioBuffer> {
    const cached = this.bufferCache.get(songId);
    if (cached) {
      return cached;
    }
    const ctx = await this.ensureContext();
    const resp = await fetch(`/v1/songs/${encodeURIComponent(songId)}/instrumental`, {
      cache: "force-cache",
    });
    if (!resp.ok) {
      throw new Error(`加载伴奏失败: HTTP ${resp.status}`);
    }
    const arrayBuffer = await resp.arrayBuffer();
    const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
    this.bufferCache.set(songId, audioBuffer);
    return audioBuffer;
  }

  async startSinging(songId: string, genId: number, existingContext?: AudioContext): Promise<void> {
    this.stopSinging(genId);
    const ctx = await this.ensureContext(existingContext);
    const instBuffer = await this.loadInstrumental(songId);

    // 预留 60ms 调度缓冲，作为 PTS = 0 的时间零点
    const startAt = ctx.currentTime + 0.06;
    this.songOriginAudioTime = startAt;
    this.activeGenId = genId;
    this.singingActive = true;

    const src = ctx.createBufferSource();
    src.buffer = instBuffer;
    src.connect(this.instrumentalGain!);
    src.onended = () => {
      if (this.instrumentalSource === src) {
        this.singingActive = false;
      }
    };
    src.start(startAt);
    this.instrumentalSource = src;
  }

  /**
   * 调度 0x03 纯人声干声流，按 frame.ptsMs 与伴奏时间零点精确对齐。
   */
  pushVocalFrame(pcm16Bytes: Uint8Array, genId: number, ptsMs: number, sampleRate = 24000): void {
    if (!this.audioCtx || !this.vocalGain || !this.singingActive || genId < this.activeGenId) {
      return;
    }

    const int16 = new Int16Array(
      pcm16Bytes.buffer,
      pcm16Bytes.byteOffset,
      Math.floor(pcm16Bytes.byteLength / 2)
    );
    if (int16.length === 0) return;

    const audioBuf = this.audioCtx.createBuffer(1, int16.length, sampleRate);
    const channelData = audioBuf.getChannelData(0);
    for (let i = 0; i < int16.length; i++) {
      channelData[i] = int16[i] / 32768.0;
    }

    const targetTime = this.songOriginAudioTime + ptsMs / 1000;
    const now = this.audioCtx.currentTime;
    const delayDiff = targetTime - now;

    // 若网络抖动导致帧迟到超过整帧时长，跳过过期部分保持口型与伴奏不漂移
    let offsetSec = 0;
    let scheduleAt = targetTime;
    if (delayDiff < 0) {
      const lateSec = -delayDiff;
      if (lateSec >= audioBuf.duration) {
        return;
      }
      offsetSec = lateSec;
      scheduleAt = now;
    }

    const src = this.audioCtx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(this.vocalGain);
    this.scheduledVocalSources.add(src);
    src.onended = () => {
      this.scheduledVocalSources.delete(src);
    };
    src.start(scheduleAt, offsetSec);
  }

  stopSinging(nextGenId?: number): void {
    if (typeof nextGenId === "number") {
      this.activeGenId = Math.max(this.activeGenId, nextGenId);
    }
    this.singingActive = false;

    try {
      this.instrumentalSource?.stop();
    } catch {
      /* 已停止 */
    }
    this.instrumentalSource?.disconnect();
    this.instrumentalSource = null;

    for (const src of this.scheduledVocalSources) {
      try {
        src.stop();
      } catch {
        /* 已停止 */
      }
      src.disconnect();
    }
    this.scheduledVocalSources.clear();
  }
}
