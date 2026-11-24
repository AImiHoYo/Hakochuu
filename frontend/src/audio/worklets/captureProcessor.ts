export interface CaptureFrameEvent {
  pcm16: Uint8Array;
  float32: Float32Array;
  rms: number;
  ptsMs: number;
}

const CAPTURE_WORKLET_CODE = `
class HakochuuCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const params = (options && options.processorOptions) || {};
    this.frameSamples = params.frameSamples || 480; // 20ms @ 24kHz
    this.buffer = new Float32Array(this.frameSamples);
    this.offset = 0;
    this.muted = false;
    this.startTimeMs = Date.now();

    this.port.onmessage = (event) => {
      if (event.data && event.data.type === "set_muted") {
        this.muted = Boolean(event.data.muted);
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) {
      return true;
    }
    const channel = input[0];
    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.offset++] = this.muted ? 0 : channel[i];
      if (this.offset >= this.frameSamples) {
        this.flushFrame();
      }
    }
    return true;
  }

  flushFrame() {
    const pcm16 = new Int16Array(this.frameSamples);
    const floatCopy = new Float32Array(this.frameSamples);
    let sumSq = 0;

    for (let i = 0; i < this.frameSamples; i++) {
      const s = Math.max(-1, Math.min(1, this.buffer[i]));
      floatCopy[i] = s;
      sumSq += s * s;
      pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    const rms = Math.sqrt(sumSq / this.frameSamples);
    const ptsMs = Math.round(currentTime * 1000);

    this.port.postMessage(
      {
        type: "frame",
        pcm16Buffer: pcm16.buffer,
        floatBuffer: floatCopy.buffer,
        rms,
        ptsMs,
      },
      [pcm16.buffer, floatCopy.buffer]
    );
    this.offset = 0;
  }
}

registerProcessor("hakochuu-capture-processor", HakochuuCaptureProcessor);
`;

export class AudioCaptureEngine {
  private audioCtx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private sampleRate: number;
  private frameMs: number;

  constructor(sampleRate = 24000, frameMs = 20) {
    this.sampleRate = sampleRate;
    this.frameMs = frameMs;
  }

  async start(
    onFrame: (frame: CaptureFrameEvent) => void,
    existingContext?: AudioContext
  ): Promise<AudioContext> {
    if (this.workletNode && this.audioCtx) {
      return this.audioCtx;
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        sampleRate: this.sampleRate,
        channelCount: 1,
      },
    });

    this.audioCtx =
      existingContext ?? new AudioContext({ sampleRate: this.sampleRate, latencyHint: "interactive" });
    if (this.audioCtx.state === "suspended") {
      await this.audioCtx.resume();
    }

    const blob = new Blob([CAPTURE_WORKLET_CODE], { type: "application/javascript" });
    const moduleUrl = URL.createObjectURL(blob);
    try {
      await this.audioCtx.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }

    const actualRate = this.audioCtx.sampleRate;
    const frameSamples = Math.max(128, Math.round((actualRate * this.frameMs) / 1000));

    this.sourceNode = this.audioCtx.createMediaStreamSource(this.stream);
    this.workletNode = new AudioWorkletNode(this.audioCtx, "hakochuu-capture-processor", {
      processorOptions: { frameSamples },
    });

    this.workletNode.port.onmessage = (event: MessageEvent) => {
      const data = event.data as {
        type: string;
        pcm16Buffer: ArrayBuffer;
        floatBuffer: ArrayBuffer;
        rms: number;
        ptsMs: number;
      };
      if (data?.type === "frame") {
        onFrame({
          pcm16: new Uint8Array(data.pcm16Buffer),
          float32: new Float32Array(data.floatBuffer),
          rms: data.rms,
          ptsMs: data.ptsMs,
        });
      }
    };

    // 连接至静音增益节点以驱动 AudioWorklet 运行但不产生本地回声
    const silentGain = this.audioCtx.createGain();
    silentGain.gain.value = 0;
    this.sourceNode.connect(this.workletNode);
    this.workletNode.connect(silentGain);
    silentGain.connect(this.audioCtx.destination);

    return this.audioCtx;
  }

  setMuted(muted: boolean): void {
    this.workletNode?.port.postMessage({ type: "set_muted", muted });
  }

  async stop(): Promise<void> {
    this.workletNode?.disconnect();
    this.sourceNode?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.workletNode = null;
    this.sourceNode = null;
    this.stream = null;
  }
}
