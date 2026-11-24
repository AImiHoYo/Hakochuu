const PLAYBACK_WORKLET_CODE = `
class HakochuuPlaybackProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const params = (options && options.processorOptions) || {};
    this.capacity = params.capacitySamples || 24000 * 6; // 默认 6 秒环形抖动缓冲
    this.ring = new Float32Array(this.capacity);
    this.readIdx = 0;
    this.writeIdx = 0;
    this.buffered = 0;
    this.activeGenId = 1;

    this.port.onmessage = (event) => {
      const msg = event.data;
      if (!msg) return;

      if (msg.type === "flush") {
        this.activeGenId = Math.max(this.activeGenId, msg.genId || this.activeGenId);
        this.readIdx = 0;
        this.writeIdx = 0;
        this.buffered = 0;
        this.port.postMessage({ type: "state", isPlaying: false, bufferedSamples: 0 });
        return;
      }

      if (msg.type === "push") {
        if ((msg.genId || 0) < this.activeGenId) {
          return;
        }
        const int16 = new Int16Array(msg.pcm16Buffer);
        for (let i = 0; i < int16.length; i++) {
          if (this.buffered >= this.capacity) {
            // 缓冲区满时丢弃最旧样本，避免延迟雪崩
            this.readIdx = (this.readIdx + 1) % this.capacity;
            this.buffered--;
          }
          this.ring[this.writeIdx] = int16[i] / 32768.0;
          this.writeIdx = (this.writeIdx + 1) % this.capacity;
          this.buffered++;
        }
      }
    };
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    if (!output || !output[0]) {
      return true;
    }
    const channel = output[0];
    const hadAudio = this.buffered > 0;

    for (let i = 0; i < channel.length; i++) {
      if (this.buffered > 0) {
        channel[i] = this.ring[this.readIdx];
        this.readIdx = (this.readIdx + 1) % this.capacity;
        this.buffered--;
      } else {
        channel[i] = 0;
      }
    }

    if (hadAudio && this.buffered === 0) {
      this.port.postMessage({ type: "state", isPlaying: false, bufferedSamples: 0 });
    }
    return true;
  }
}

registerProcessor("hakochuu-playback-processor", HakochuuPlaybackProcessor);
`;

export class AudioPlaybackEngine {
  private audioCtx: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private activeGenId = 1;
  private playing = false;
  private onPlayingChange?: (isPlaying: boolean) => void;

  constructor(onPlayingChange?: (isPlaying: boolean) => void) {
    this.onPlayingChange = onPlayingChange;
  }

  get isPlaying(): boolean {
    return this.playing;
  }

  async init(audioCtx: AudioContext): Promise<void> {
    if (this.workletNode && this.audioCtx === audioCtx) {
      return;
    }
    this.audioCtx = audioCtx;
    const blob = new Blob([PLAYBACK_WORKLET_CODE], { type: "application/javascript" });
    const moduleUrl = URL.createObjectURL(blob);
    try {
      await audioCtx.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }

    this.workletNode = new AudioWorkletNode(audioCtx, "hakochuu-playback-processor", {
      processorOptions: { capacitySamples: audioCtx.sampleRate * 6 },
    });

    this.workletNode.port.onmessage = (event: MessageEvent) => {
      const msg = event.data as { type: string; isPlaying?: boolean };
      if (msg?.type === "state" && typeof msg.isPlaying === "boolean") {
        this.setPlaying(msg.isPlaying);
      }
    };

    this.workletNode.connect(audioCtx.destination);
  }

  enqueuePcm16(pcm16Bytes: Uint8Array, genId: number, ptsMs: number): void {
    if (!this.workletNode || genId < this.activeGenId) {
      return;
    }
    // 拷贝独立 ArrayBuffer 以便 Transferable 零拷贝送入音频线程
    const copy = new Uint8Array(pcm16Bytes.byteLength);
    copy.set(pcm16Bytes);
    this.setPlaying(true);
    this.workletNode.port.postMessage(
      {
        type: "push",
        pcm16Buffer: copy.buffer,
        genId,
        ptsMs,
      },
      [copy.buffer]
    );
  }

  flush(nextGenId: number): void {
    this.activeGenId = Math.max(this.activeGenId, nextGenId);
    this.setPlaying(false);
    this.workletNode?.port.postMessage({
      type: "flush",
      genId: this.activeGenId,
    });
  }

  dispose(): void {
    this.workletNode?.disconnect();
    this.workletNode = null;
    this.setPlaying(false);
  }

  private setPlaying(next: boolean): void {
    if (this.playing !== next) {
      this.playing = next;
      this.onPlayingChange?.(next);
    }
  }
}
