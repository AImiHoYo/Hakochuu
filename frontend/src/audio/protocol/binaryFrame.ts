export const HEADER_SIZE = 16;
export const PROTOCOL_VERSION = 1;

export const FrameFlags = {
  NONE: 0,
  EOS: 1 << 0,
  KEYFRAME: 1 << 1,
} as const;

export const FrameType = {
  USER_AUDIO_UP: 0x01,
  TTS_AUDIO_DOWN: 0x02,
  VC_VOCAL_DOWN: 0x03,
  AVATAR_VISEME_DOWN: 0x04,
} as const;

export type FrameTypeValue = (typeof FrameType)[keyof typeof FrameType];

export const CodecId = {
  PCM16_16K: 1,
  PCM16_24K: 2,
  OPUS_24K: 3,
  OPUS_48K: 4,
  VISEME_MSGPACK: 5,
} as const;

export type CodecIdValue = (typeof CodecId)[keyof typeof CodecId];

export interface BinaryFrame {
  version: number;
  flags: number;
  frameType: FrameTypeValue;
  codec: CodecIdValue;
  genId: number;
  ptsMs: number;
  payload: Uint8Array;
}

export function packBinaryFrame(frame: Omit<BinaryFrame, "version"> & { version?: number }): ArrayBuffer {
  const version = frame.version ?? PROTOCOL_VERSION;
  const buffer = new ArrayBuffer(HEADER_SIZE + frame.payload.byteLength);
  const view = new DataView(buffer);

  const verFlags = ((version & 0x0f) << 4) | (frame.flags & 0x0f);
  view.setUint8(0, verFlags);
  view.setUint8(1, frame.frameType);
  view.setUint16(2, frame.codec, false); // Big-Endian
  view.setUint32(4, frame.genId, false);
  view.setUint32(8, frame.ptsMs, false);
  view.setUint32(12, frame.payload.byteLength, false);

  new Uint8Array(buffer, HEADER_SIZE).set(frame.payload);
  return buffer;
}

export function unpackBinaryFrame(buffer: ArrayBuffer): BinaryFrame {
  if (buffer.byteLength < HEADER_SIZE) {
    throw new Error(`Frame too short: ${buffer.byteLength} < ${HEADER_SIZE}`);
  }
  const view = new DataView(buffer);
  const verFlags = view.getUint8(0);
  const frameType = view.getUint8(1) as FrameTypeValue;
  const codec = view.getUint16(2, false) as CodecIdValue;
  const genId = view.getUint32(4, false);
  const ptsMs = view.getUint32(8, false);
  const payloadLen = view.getUint32(12, false);

  if (buffer.byteLength !== HEADER_SIZE + payloadLen) {
    throw new Error(
      `Payload length mismatch: got ${buffer.byteLength - HEADER_SIZE}, expected ${payloadLen}`
    );
  }

  return {
    version: (verFlags >> 4) & 0x0f,
    flags: verFlags & 0x0f,
    frameType,
    codec,
    genId,
    ptsMs,
    payload: new Uint8Array(buffer, HEADER_SIZE, payloadLen),
  };
}

export function shouldDropStaleFrame(frame: BinaryFrame, activeGenId: number): boolean {
  return frame.genId < activeGenId;
}
