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


def _generate_mock_vocal_frame(frame_idx: int, sample_rate: int = 24000, ms: int = 20) -> bytes:
    """生成 20ms 纯人声测试 PCM16 帧（523Hz C5 音高区分于 TTS），供双轨混音对齐测试。"""
    num_samples = int(sample_rate * ms / 1000)
    freq = 523.25
    samples = [
        int(3500 * math.sin(2 * math.pi * freq * ((frame_idx * num_samples + i) / sample_rate)))
        for i in range(num_samples)
    ]
    return struct.pack(f"<{num_samples}h", *samples)


class VcEngine(BaseAudioEngine):
    """零样本 VC 翻唱引擎（Seed-VC 远端服务或 Mock 模式，仅输出纯人声干声流）。"""

    name = "seed_vc"
    estimated_vram_mb = 5000

    def __init__(
        self,
        server_url: str | None = None,
        mock_mode: bool | None = None,
    ) -> None:
        self.server_url = (
            server_url or os.getenv("HAKOCHUU_VC_SERVER_URL", "http://localhost:23335")
        ).rstrip("/")
        self.mock_mode = _resolve_mock_flag("HAKOCHUU_VC_MOCK", mock_mode)

    async def load_to_gpu(self) -> None:
        self.location = "gpu"

    async def offload_to_pinned_cpu(self) -> None:
        self.location = "pinned_cpu"
        if not self.mock_mode:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(f"{self.server_url}/v1/vc/offload")
            except httpx.HTTPError:
                pass

    async def convert_song_stream(
        self,
        song_id: str,
        character_id: str,
        key_shift: int = 0,
        ref_sing_path: str | None = None,
    ) -> AsyncGenerator[tuple[int, bytes], None]:
        """流式输出 (pts_ms, pure_vocal_pcm16_bytes)，严格对齐 960B (20ms)。"""
        if self.mock_mode:
            for idx in range(10):
                yield idx * 20, _generate_mock_vocal_frame(idx)
            return

        payload = {
            "song_id": song_id,
            "character_id": character_id,
            "key_shift": key_shift,
            "ref_sing_path": ref_sing_path,
            "sample_rate": 24000,
        }
        pts_ms = 0
        buf = bytearray()
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{self.server_url}/v1/vc/stream", json=payload
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    while len(buf) >= FRAME_BYTES_24K_20MS:
                        yield pts_ms, bytes(buf[:FRAME_BYTES_24K_20MS])
                        del buf[:FRAME_BYTES_24K_20MS]
                        pts_ms += 20
        if buf:
            yield pts_ms, bytes(buf) + b"\x00" * (FRAME_BYTES_24K_20MS - len(buf))
