import {
  BinaryFrame,
  CodecId,
  CodecIdValue,
  FrameFlags,
  FrameType,
  FrameTypeValue,
  packBinaryFrame,
  shouldDropStaleFrame,
  unpackBinaryFrame,
} from "../protocol/binaryFrame";

export interface HelloAckMessage {
  type: "HELLO_ACK";
  protocol_version: string;
  deploy_mode: "local" | "lan";
  negotiated_codec: CodecIdValue;
  active_gen_id: number;
}

export interface WsClientCallbacks {
  onOpen?: () => void;
  onClose?: () => void;
  onSignal?: (msg: Record<string, unknown>) => void;
  onBinaryFrame?: (frame: BinaryFrame) => void;
  onDropStale?: (frame: BinaryFrame) => void;
  onError?: (err: string) => void;
}

export class HakochuuWsClient {
  private ws: WebSocket | null = null;
  private activeGenId = 1;
  private negotiatedCodec: CodecIdValue = CodecId.PCM16_24K;
  private callbacks: WsClientCallbacks;

  constructor(callbacks: WsClientCallbacks = {}) {
    this.callbacks = callbacks;
  }

  get genId(): number {
    return this.activeGenId;
  }

  get codec(): CodecIdValue {
    return this.negotiatedCodec;
  }

  connect(deployMode: "local" | "lan" = "local", characterId = "char_001"): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      return;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/v1/session`;
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      this.sendSignal({
        type: "HELLO",
        protocol_version: "1.0",
        deploy_mode: deployMode,
        capabilities: ["wasm_vad", "client_mixer", "avatar_viseme_v1"],
        character_id: characterId,
      });
      this.callbacks.onOpen?.();
    };

    this.ws.onmessage = (event: MessageEvent) => {
      if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data) as Record<string, unknown>;
          if (msg.type === "HELLO_ACK") {
            const ack = msg as unknown as HelloAckMessage;
            this.negotiatedCodec = ack.negotiated_codec;
            this.activeGenId = Math.max(this.activeGenId, ack.active_gen_id || 1);
          }
          this.callbacks.onSignal?.(msg);
        } catch {
          this.callbacks.onError?.("收到无法解析的 JSON 信令");
        }
        return;
      }

      if (event.data instanceof ArrayBuffer) {
        try {
          const frame = unpackBinaryFrame(event.data);
          if (shouldDropStaleFrame(frame, this.activeGenId)) {
            this.callbacks.onDropStale?.(frame);
            return;
          }
          this.callbacks.onBinaryFrame?.(frame);
        } catch (err) {
          this.callbacks.onError?.(err instanceof Error ? err.message : String(err));
        }
      }
    };

    this.ws.onerror = () => {
      this.callbacks.onError?.("WebSocket 连接发生错误");
    };

    this.ws.onclose = () => {
      this.callbacks.onClose?.();
      this.ws = null;
    };
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
  }

  interrupt(): number {
    this.activeGenId += 1;
    this.sendSignal({
      type: "INTERRUPT",
      gen_id: this.activeGenId,
    });
    return this.activeGenId;
  }

  sendSignal(payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  sendAudioFrame(
    payload: Uint8Array,
    ptsMs: number,
    options?: {
      frameType?: FrameTypeValue;
      codec?: CodecIdValue;
      flags?: number;
      genIdOverride?: number;
    }
  ): void {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      return;
    }
    const packet = packBinaryFrame({
      frameType: options?.frameType ?? FrameType.USER_AUDIO_UP,
      codec: options?.codec ?? this.negotiatedCodec,
      genId: options?.genIdOverride ?? this.activeGenId,
      ptsMs,
      flags: options?.flags ?? FrameFlags.NONE,
      payload,
    });
    this.ws.send(packet);
  }
}
