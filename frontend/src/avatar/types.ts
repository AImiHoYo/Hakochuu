export type AvatarType = "none" | "live2d" | "vrm" | "webrtc_video";
export type LipSyncMode = "rms" | "viseme";

export interface VisemeWeights {
  aa: number;
  ih: number;
  ou: number;
  ee: number;
  oh: number;
  rms: number;
}

export interface EmotionTag {
  emotion: string;
  action?: string;
}

export interface AvatarRenderer {
  readonly type: AvatarType;
  mount(container: HTMLElement, assetUrl?: string): Promise<void>;
  applyLipSync(weights: VisemeWeights, ptsMs: number): void;
  applyEmotion(tag: EmotionTag): void;
  dispose(): void;
}
