import os
from pathlib import Path

import httpx


def _resolve_mock_flag(env_key: str, explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    specific = os.getenv(env_key)
    if specific is not None and specific != "":
        return specific.lower() == "true"
    return os.getenv("HAKOCHUU_MOCK_ENGINES", "true").lower() == "true"


class AsrEngine:
    """语音识别引擎（SenseVoice / Faster-Whisper 远端服务或本地 Mock 模式）。"""

    def __init__(
        self,
        server_url: str | None = None,
        mock_mode: bool | None = None,
    ) -> None:
        self.server_url = (
            server_url or os.getenv("HAKOCHUU_ASR_SERVER_URL", "http://localhost:23336")
        ).rstrip("/")
        self.mock_mode = _resolve_mock_flag("HAKOCHUU_ASR_MOCK", mock_mode)

    async def transcribe_pcm16(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 24000,
        language: str = "zh",
    ) -> str:
        if not pcm_bytes:
            return ""
        if self.mock_mode:
            duration_ms = int(len(pcm_bytes) / 2 / sample_rate * 1000)
            return f"你好，这是模拟语音识别文本（{duration_ms}ms）"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.server_url}/v1/asr/transcribe",
                data={"sample_rate": str(sample_rate), "language": language},
                files={"file": ("utterance.pcm", pcm_bytes, "application/octet-stream")},
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("text", "")).strip()

    async def annotate_onboarding_wav(self, wav_path: Path) -> str:
        if not wav_path.is_file():
            return ""
        if self.mock_mode:
            return "引导录音自动标注示例文本"

        async with httpx.AsyncClient(timeout=30.0) as client:
            with wav_path.open("rb") as f:
                resp = await client.post(
                    f"{self.server_url}/v1/asr/transcribe",
                    files={"file": (wav_path.name, f, "audio/wav")},
                )
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("text", "")).strip()
