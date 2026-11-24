import { AudioCaptureEngine } from "./worklets/captureProcessor";

export interface RecordedClip {
  blob: Blob;
  url: string;
  durationSec: number;
  sha256: string;
  filename: string;
}

function encodeWavPcm16(chunks: Uint8Array[], sampleRate: number): ArrayBuffer {
  const totalPcmBytes = chunks.reduce((acc, c) => acc + c.byteLength, 0);
  const buffer = new ArrayBuffer(44 + totalPcmBytes);
  const view = new DataView(buffer);

  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  writeStr(0, "RIFF");
  view.setUint32(4, 36 + totalPcmBytes, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, "data");
  view.setUint32(40, totalPcmBytes, true);

  const out = new Uint8Array(buffer, 44);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return buffer;
}

async function sha256Hex(buffer: ArrayBuffer): Promise<string> {
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export class GuidedAudioRecorder {
  private capture: AudioCaptureEngine | null = null;
  private chunks: Uint8Array[] = [];
  private sampleRate = 24000;
  private startedAt = 0;

  async start(onLevel?: (rms: number) => void): Promise<void> {
    this.chunks = [];
    this.capture = new AudioCaptureEngine(this.sampleRate, 20);
    const ctx = await this.capture.start((frame) => {
      const copy = new Uint8Array(frame.pcm16.byteLength);
      copy.set(frame.pcm16);
      this.chunks.push(copy);
      onLevel?.(frame.rms);
    });
    this.sampleRate = ctx.sampleRate;
    this.startedAt = performance.now();
  }

  async stop(filename = "recording.wav"): Promise<RecordedClip> {
    await this.capture?.stop();
    this.capture = null;

    const durationSec = Math.max(0.1, (performance.now() - this.startedAt) / 1000);
    const wavBuffer = encodeWavPcm16(this.chunks, this.sampleRate);
    const sha256 = await sha256Hex(wavBuffer);
    const blob = new Blob([wavBuffer], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);

    return { blob, url, durationSec, sha256, filename };
  }
}
