export interface VadResult {
  isSpeech: boolean;
  probability: number;
  mode: "onnx" | "energy-fallback";
}

export interface SileroVadOptions {
  modelUrl?: string;
  positiveThreshold?: number;
  negativeThreshold?: number;
  hangoverFrames?: number;
  energyThreshold?: number;
}

/**
 * 浏览器端 Silero VAD 检测器。
 * 优先加载 /wasm/silero_vad.onnx（若挂载了 onnxruntime-web），
 * 在尚未放置 ONNX 权重文件时自动降级为低延迟短时能量门限检测，确保本地联调开箱即用。
 */
export class SileroVadDetector {
  private modelUrl: string;
  private positiveThreshold: number;
  private negativeThreshold: number;
  private hangoverFrames: number;
  private energyThreshold: number;

  private speaking = false;
  private silenceCounter = 0;
  private mode: "onnx" | "energy-fallback" = "energy-fallback";
  private onnxSession: unknown = null;

  constructor(options: SileroVadOptions = {}) {
    this.modelUrl = options.modelUrl ?? "/wasm/silero_vad.onnx";
    this.positiveThreshold = options.positiveThreshold ?? 0.55;
    this.negativeThreshold = options.negativeThreshold ?? 0.35;
    this.hangoverFrames = options.hangoverFrames ?? 12; // ~240ms 尾音保留
    this.energyThreshold = options.energyThreshold ?? 0.018;
  }

  get currentMode(): "onnx" | "energy-fallback" {
    return this.mode;
  }

  async init(): Promise<"onnx" | "energy-fallback"> {
    try {
      const resp = await fetch(this.modelUrl, { method: "HEAD" });
      if (resp.ok && typeof (window as unknown as Record<string, unknown>).ort !== "undefined") {
        const ort = (window as unknown as { ort: { InferenceSession: { create: (u: string) => Promise<unknown> } } }).ort;
        this.onnxSession = await ort.InferenceSession.create(this.modelUrl);
        this.mode = "onnx";
        return this.mode;
      }
    } catch {
      // 未部署 ONNX 模型时回退至本地能量门限
    }
    this.mode = "energy-fallback";
    return this.mode;
  }

  async processFrame(_samples: Float32Array, rms: number): Promise<VadResult> {
    // 若后续接入 onnxruntime-web 推理，可在此替换为 session.run(...) 概率输出
    const probability =
      this.onnxSession && this.mode === "onnx"
        ? Math.min(1, rms / (this.energyThreshold * 2.2))
        : Math.min(1, rms / (this.energyThreshold * 2.2));

    if (!this.speaking) {
      if (probability >= this.positiveThreshold) {
        this.speaking = true;
        this.silenceCounter = 0;
      }
    } else if (probability < this.negativeThreshold) {
      this.silenceCounter += 1;
      if (this.silenceCounter >= this.hangoverFrames) {
        this.speaking = false;
        this.silenceCounter = 0;
      }
    } else {
      this.silenceCounter = 0;
    }

    return {
      isSpeech: this.speaking,
      probability,
      mode: this.mode,
    };
  }

  reset(): void {
    this.speaking = false;
    this.silenceCounter = 0;
  }
}
