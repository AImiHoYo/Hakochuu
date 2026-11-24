import os
from dataclasses import asdict

from fastapi import APIRouter

from app.core.gpu_manager import get_gpu_manager

router = APIRouter(prefix="/system", tags=["system"])


def _is_mock(env_key: str) -> bool:
    specific = os.getenv(env_key)
    if specific is not None and specific != "":
        return specific.lower() == "true"
    return os.getenv("HAKOCHUU_MOCK_ENGINES", "true").lower() == "true"


@router.get("/capabilities")
async def get_capabilities() -> dict[str, object]:
    deploy_mode = os.getenv("HAKOCHUU_DEPLOY_MODE", "local")
    mock_engines = os.getenv("HAKOCHUU_MOCK_ENGINES", "true").lower() == "true"
    return {
        "protocol_version": "1.0",
        "deploy_mode": deploy_mode,
        "mock_engines": mock_engines,
        "engines": {
            "asr_mock": _is_mock("HAKOCHUU_ASR_MOCK"),
            "tts_mock": _is_mock("HAKOCHUU_TTS_MOCK"),
            "vc_mock": _is_mock("HAKOCHUU_VC_MOCK"),
        },
        "supported_codecs": ["pcm16_24k", "opus_24k", "opus_48k"],
        "features": {
            "wasm_vad": True,
            "client_mixer": True,
            "avatar_viseme_v1": True,
        },
    }


@router.get("/gpu")
async def get_gpu_status() -> dict[str, object]:
    return asdict(get_gpu_manager().get_status())
