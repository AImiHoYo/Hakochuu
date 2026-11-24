import math
import os
import struct
from collections.abc import AsyncGenerator

import httpx

from app.engines.base import BaseAudioEngine

FRAME_BYTES_24K_20MS = 960


def _resolve_mock_flag(env_key: str, explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    specific = os.getenv(env_key)
    if specific is not None and specific != "":
        return specific.lower() == "true"
    return os.getenv("HAKOCHUU_MOCK_ENGINES", "true").lower() == "true"


def _generate_mock_pcm16_frame(frame_idx: int, sample_rate: int = 24000, ms: int = 20) -> bytes:
    """生成 20ms PCM16 单声道测试音频帧，供无 GPU 开发机端到端播放联调。"""
    num_samples = int(sample_rate * ms / 1000)
    freq = 440.0
    samples = [
        int(3000 * math.sin(2 * math.pi * freq * ((frame_idx * num_samples + i) / sample_rate)))
        for i in range(num_samples)
    ]
    return struct.pack(f"<{num_samples}h", *samples)


class TtsEngine(BaseAudioEngine):
    """流式对话 TTS 引擎（CosyVoice 3 / Qwen3-TTS 远端服务或 Mock 模式）。"""

    name = "cosyvoice3_qwen3_tts"
    estimated_vram_mb = 4500

    def __init__(
        self,
        server_url: str | None = None,
        mock_mode: bool | None = None,
    ) -> None:
        self.server_url = (
            server_url or os.getenv("HAKOCHUU_TTS_SERVER_URL", "http://localhost:23334")
        ).rstrip("/")
        self.mock_mode = _resolve_mock_flag("HAKOCHUU_TTS_MOCK", mock_mode)
        self.active_lora_path: str | None = None

    async def load_to_gpu(self) -> None:
        self.location = "gpu"

    async def offload_to_pinned_cpu(self) -> None:
        self.location = "pinned_cpu"
        if not self.mock_mode:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(f"{self.server_url}/v1/tts/offload")
            except httpx.HTTPError:
                pass

    def attach_lora(self, lora_path: str | None) -> None:
        self.active_lora_path = lora_path

    async def synthesize_stream(
        self,
        text: str,
        character_id: str,
        emotion: str = "neutral",
        ref_speech_path: str | None = None,
        ref_speech_text: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        if self.mock_mode:
            frame_count = max(3, min(15, len(text)))
            for idx in range(frame_count):
                yield _generate_mock_pcm16_frame(idx)
            return

        payload = {
            "text": text,
            "character_id": character_id,
            "emotion": emotion,
            "lora_path": self.active_lora_path,
            "ref_speech_path": ref_speech_path,
            "ref_speech_text": ref_speech_text,
            "sample_rate": 24000,
        }
        buf = bytearray()
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST", f"{self.server_url}/v1/tts/stream", json=payload
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    while len(buf) >= FRAME_BYTES_24K_20MS:
                        yield bytes(buf[:FRAME_BYTES_24K_20MS])
                        del buf[:FRAME_BYTES_24K_20MS]
        if buf:
            # 尾部不足 20ms 时补零对齐 960B 帧长，防止播放抖动
            yield bytes(buf) + b"\x00" * (FRAME_BYTES_24K_20MS - len(buf))
