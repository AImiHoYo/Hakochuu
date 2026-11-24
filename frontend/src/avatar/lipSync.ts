import { AvatarRenderer, EmotionTag, VisemeWeights } from "./types";

const DEFAULT_WEIGHTS: VisemeWeights = {
  aa: 0,
  ih: 0,
  ou: 0,
  ee: 0,
  oh: 0,
  rms: 0,
};

const TAG_REGEX = /\[(emotion|action):([a-zA-Z0-9_-]+)\]/g;

/**
 * 剥离 LLM 输出前缀中的结构化情绪/动作标签（如 [emotion:cheerful][action:nod]），
 * 返回供 TTS 朗读的纯文本与透传给虚拟形象的标签列表。
 */
export function parseEmotionTags(text: string): {
  cleanText: string;
  tags: EmotionTag[];
} {
  const tags: EmotionTag[] = [];
  let currentEmotion: string | null = null;

  const cleanText = text
    .replace(TAG_REGEX, (_match, key: string, value: string) => {
      if (key === "emotion") {
        currentEmotion = value;
        tags.push({ emotion: value });
      } else if (key === "action") {
        if (tags.length > 0 && currentEmotion) {
          tags[tags.length - 1].action = value;
        } else {
          tags.push({ emotion: currentEmotion ?? "neutral", action: value });
        }
      }
      return "";
    })
    .trim();

  return { cleanText, tags };
}

/**
 * 预留虚拟形象口型同步控制器：
 * 接收 0x04 (AVATAR_VISEME_DOWN) 二进制帧，按 PTS 驱动 Live2D / VRM 渲染器。
 */
export class LipSyncController {
  private renderer: AvatarRenderer | null = null;
  private current: VisemeWeights = { ...DEFAULT_WEIGHTS };
  private smoothing: number;

  constructor(smoothing = 0.45) {
    this.smoothing = smoothing;
  }

  attachRenderer(renderer: AvatarRenderer | null): void {
    this.renderer = renderer;
  }

  get currentWeights(): VisemeWeights {
    return this.current;
  }

  ingestFramePayload(payload: Uint8Array, ptsMs: number): VisemeWeights {
    let parsed: Partial<VisemeWeights> = {};
    try {
      const text = new TextDecoder().decode(payload);
      parsed = JSON.parse(text) as Partial<VisemeWeights>;
    } catch {
      // 若为紧凑单字节/浮点包，后续可在此扩展 MessagePack 解码
      parsed = { rms: payload.length > 0 ? payload[0] / 255 : 0 };
    }

    const alpha = this.smoothing;
    this.current = {
      aa: this.lerp(this.current.aa, parsed.aa ?? parsed.rms ?? 0, alpha),
      ih: this.lerp(this.current.ih, parsed.ih ?? 0, alpha),
      ou: this.lerp(this.current.ou, parsed.ou ?? 0, alpha),
      ee: this.lerp(this.current.ee, parsed.ee ?? 0, alpha),
      oh: this.lerp(this.current.oh, parsed.oh ?? 0, alpha),
      rms: this.lerp(this.current.rms, parsed.rms ?? 0, alpha),
    };

    this.renderer?.applyLipSync(this.current, ptsMs);
    return this.current;
  }

  reset(): void {
    this.current = { ...DEFAULT_WEIGHTS };
    this.renderer?.applyLipSync(this.current, 0);
  }

  private lerp(prev: number, next: number, alpha: number): number {
    return prev * (1 - alpha) + Math.max(0, Math.min(1, next)) * alpha;
  }
}
