"""独立 ASR 语音识别微服务（默认监听 23336 端口，运行于 GPU 服务端）。

支持从 shared/models_manifest.yaml 读取引擎与权重路径：
- sensevoice (funasr AutoModel)
- faster_whisper (faster_whisper.WhisperModel)
"""

from __future__ import annotations

import io
import os
import struct
import time
import wave
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, File, Form, UploadFile

from app.core.logging import setup_logging

logger = setup_logging("asr")

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "shared" / "models_manifest.yaml"

_MODEL_INSTANCE: Any = None
_MODEL_ENGINE: str = "sensevoice"
_WEIGHTS_DIR: str = ""


def _load_asr_manifest() -> tuple[str, str]:
    engine = os.getenv("HAKOCHUU_ASR_ENGINE", "").strip()
    weights_dir = os.getenv("HAKOCHUU_ASR_WEIGHTS_DIR", "").strip()
    if MANIFEST_PATH.is_file():
        data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
        asr_cfg = (data.get("models") or {}).get("asr") or {}
        if not engine:
            engine = str(asr_cfg.get("engine") or "sensevoice")
        if not weights_dir:
            weights_dir = str(asr_cfg.get("weights_dir") or "")
        cache_cfg = data.get("cache") or {}
        if cache_cfg.get("hf_home"):
            os.environ.setdefault("HF_HOME", str((REPO_ROOT / cache_cfg["hf_home"]).resolve()))
        if cache_cfg.get("torch_home"):
            os.environ.setdefault(
                "TORCH_HOME", str((REPO_ROOT / cache_cfg["torch_home"]).resolve())
            )
    return engine or "sensevoice", weights_dir


def _pcm16_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _resample_pcm16_linear(pcm_bytes: bytes, src_rate: int, dst_rate: int = 16000) -> bytes:
    if src_rate == dst_rate or not pcm_bytes:
        return pcm_bytes
    count = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{count}h", pcm_bytes[: count * 2])
    out_len = max(1, int(count * dst_rate / src_rate))
    ratio = count / out_len
    resampled = []
    for i in range(out_len):
        pos = i * ratio
        idx = min(int(pos), count - 1)
        nxt = min(idx + 1, count - 1)
        frac = pos - idx
        val = int(samples[idx] * (1.0 - frac) + samples[nxt] * frac)
        resampled.append(max(-32768, min(32767, val)))
    return struct.pack(f"<{len(resampled)}h", *resampled)


def _get_or_load_model() -> tuple[str, Any]:
    global _MODEL_INSTANCE, _MODEL_ENGINE, _WEIGHTS_DIR
    if _MODEL_INSTANCE is not None:
        return _MODEL_ENGINE, _MODEL_INSTANCE

    engine, weights_dir = _load_asr_manifest()
    _MODEL_ENGINE = engine
    _WEIGHTS_DIR = weights_dir

    t0 = time.perf_counter()
    if engine == "sensevoice":
        try:
            from funasr import AutoModel  # type: ignore[import-not-found]
        except ImportError as exc:
            logger.exception("服务端未安装 funasr")
            raise RuntimeError("服务端未安装 funasr，请先安装 backend[asr]") from exc

        model_source = (
            weights_dir if weights_dir and Path(weights_dir).exists() else "iic/SenseVoiceSmall"
        )
        device = os.getenv("HAKOCHUU_ASR_DEVICE", "cuda:0")
        _MODEL_INSTANCE = AutoModel(model=model_source, device=device, disable_update=True)
        logger.info(
            "ASR 模型加载完成 engine=%s source=%s device=%s elapsed_ms=%.1f",
            engine,
            model_source,
            device,
            (time.perf_counter() - t0) * 1000,
        )
        return _MODEL_ENGINE, _MODEL_INSTANCE

    if engine == "faster_whisper":
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            logger.exception("服务端未安装 faster-whisper")
            raise RuntimeError("服务端未安装 faster-whisper") from exc

        model_source = (
            weights_dir if weights_dir and Path(weights_dir).exists() else "large-v3-turbo"
        )
        device = os.getenv("HAKOCHUU_ASR_DEVICE", "cuda")
        _MODEL_INSTANCE = WhisperModel(model_source, device=device, compute_type="float16")
        logger.info(
            "ASR 模型加载完成 engine=%s source=%s device=%s elapsed_ms=%.1f",
            engine,
            model_source,
            device,
            (time.perf_counter() - t0) * 1000,
        )
        return _MODEL_ENGINE, _MODEL_INSTANCE

    raise RuntimeError(f"不支持的 ASR 引擎: {engine}")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    _get_or_load_model()
    yield


app = FastAPI(title="Hakochuu ASR Service", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    engine, weights_dir = _load_asr_manifest()
    return {
        "status": "ok",
        "engine": engine,
        "weights_dir": weights_dir,
        "weights_exists": bool(weights_dir and Path(weights_dir).exists()),
        "loaded": _MODEL_INSTANCE is not None,
    }


@app.post("/v1/asr/transcribe")
async def transcribe(
    file: UploadFile = File(...),  # noqa: B008
    sample_rate: int = Form(24000),  # noqa: B008
    language: str = Form("zh"),  # noqa: B008
) -> dict[str, str]:
    raw = await file.read()
    if not raw:
        return {"text": ""}

    # 若上传的是裸 PCM16，先重采样到 16kHz 并封装为 WAV
    if file.filename and file.filename.lower().endswith(".wav"):
        wav_bytes = raw
    else:
        pcm_16k = _resample_pcm16_linear(raw, src_rate=sample_rate, dst_rate=16000)
        wav_bytes = _pcm16_to_wav_bytes(pcm_16k, 16000)

    engine, model = _get_or_load_model()
    t0 = time.perf_counter()

    try:
        if engine == "sensevoice":
            import tempfile

            from funasr.utils.postprocess_utils import rich_transcription_postprocess  # type: ignore[import-not-found]

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                tmp.write(wav_bytes)
                tmp.flush()
                res = model.generate(
                    input=tmp.name,
                    cache={},
                    language=language or "auto",
                    use_itn=True,
                    batch_size_s=60,
                )
            raw_text = str(res[0].get("text", "")) if res else ""
            text = rich_transcription_postprocess(raw_text).strip()
        else:
            # faster_whisper
            segments, _ = model.transcribe(io.BytesIO(wav_bytes), language=language or None)
            text = "".join(seg.text for seg in segments).strip()
    except Exception:
        logger.exception("ASR 转写异常 engine=%s bytes=%d", engine, len(raw))
        raise

    logger.info(
        "ASR 转写完成 engine=%s bytes=%d elapsed_ms=%.1f text=%r",
        engine,
        len(raw),
        (time.perf_counter() - t0) * 1000,
        text[:60],
    )
    return {"text": text}
