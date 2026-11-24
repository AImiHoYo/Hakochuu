import { HakochuuWsClient } from "../transport/wsClient";
import { AudioPlaybackEngine } from "../worklets/playbackProcessor";

export interface BargeInOptions {
  cooldownMs?: number;
  onsetFramesRequired?: number;
  onBargeIn?: (newGenId: number) => void;
}

/**
 * 本地 <10ms 打断控制器：
 * 检测到用户连续开口且当前正在播音时，立即在浏览器端清空 AudioWorklet 缓冲，
 * 并自增 gen_id 发送 INTERRUPT 信令丢弃网络滞留帧。
 */
export class BargeInController {
  private wsClient: HakochuuWsClient;
  private playback: AudioPlaybackEngine;
  private cooldownMs: number;
  private onsetFramesRequired: number;
  private onBargeIn?: (newGenId: number) => void;

  private consecutiveSpeechFrames = 0;
  private lastTriggerAt = 0;

  constructor(
    wsClient: HakochuuWsClient,
    playback: AudioPlaybackEngine,
    options: BargeInOptions = {}
  ) {
    this.wsClient = wsClient;
    this.playback = playback;
    this.cooldownMs = options.cooldownMs ?? 300;
    this.onsetFramesRequired = options.onsetFramesRequired ?? 2;
    this.onBargeIn = options.onBargeIn;
  }

  handleVadFrame(isSpeech: boolean): number | null {
    if (!isSpeech) {
      this.consecutiveSpeechFrames = 0;
      return null;
    }

    this.consecutiveSpeechFrames += 1;
    if (!this.playback.isPlaying) {
      return null;
    }

    const now = performance.now();
    if (
      this.consecutiveSpeechFrames >= this.onsetFramesRequired &&
      now - this.lastTriggerAt >= this.cooldownMs
    ) {
      this.lastTriggerAt = now;
      this.consecutiveSpeechFrames = 0;
      const nextGenId = this.wsClient.interrupt();
      this.playback.flush(nextGenId);
      this.onBargeIn?.(nextGenId);
      return nextGenId;
    }
    return null;
  }

  reset(): void {
    this.consecutiveSpeechFrames = 0;
  }
}
