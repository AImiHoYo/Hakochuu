"""独立流式 TTS 语音合成微服务（默认监听 23334 端口，运行于 GPU 服务端）。

支持从 shared/models_manifest.yaml 读取引擎与权重路径：
- cosyvoice3 (CosyVoice3 / CosyVoice2 流式零样本推理)
- qwen3_tts  (Qwen3-TTS 推理)

输出格式：24kHz 单声道 PCM16 裸字节流（每 20ms 对应 960 字节）。
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys
import threading
import time
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.logging import setup_logging

logger = setup_logging("tts")

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "shared" / "models_manifest.yaml"
DEFAULT_CV3_PROMPT_TEXT = (
    "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
)

_MODEL_INSTANCE: Any = None
_MODEL_ENGINE: str = "cosyvoice3"
_WEIGHTS_DIR: str = ""
_MODEL_LOCK = threading.Lock()


class TtsStreamRequest(BaseModel):
    text: str
    character_id: str = "char_001"
    emotion: str = "neutral"
    lora_path: str | None = None
    ref_speech_path: str | None = None
    ref_speech_text: str | None = None
    sample_rate: int = 24000


def _load_tts_manifest() -> tuple[str, str]:
    engine = os.getenv("HAKOCHUU_TTS_ENGINE", "").strip()
    weights_dir = os.getenv("HAKOCHUU_TTS_WEIGHTS_DIR", "").strip()
    if MANIFEST_PATH.is_file():
        data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
        tts_cfg = (data.get("models") or {}).get("tts") or {}
        if not engine:
            engine = str(tts_cfg.get("engine") or "cosyvoice3")
        if not weights_dir:
            weights_dir = str(tts_cfg.get("weights_dir") or "")
        cache_cfg = data.get("cache") or {}
        if cache_cfg.get("hf_home"):
            os.environ.setdefault("HF_HOME", str((REPO_ROOT / cache_cfg["hf_home"]).resolve()))
        if cache_cfg.get("torch_home"):
            os.environ.setdefault(
                "TORCH_HOME", str((REPO_ROOT / cache_cfg["torch_home"]).resolve())
            )
    return engine or "cosyvoice3", weights_dir


def _cosyvoice_repo_dir() -> Path | None:
    raw = os.getenv("HAKOCHUU_COSYVOICE_REPO_DIR", "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def _resolve_repo_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_file():
        return p
    candidate = (REPO_ROOT / raw_path).resolve()
    if candidate.is_file():
        return candidate
    candidate_backend = (REPO_ROOT / "backend" / raw_path).resolve()
    if candidate_backend.is_file():
        return candidate_backend
    return None


def _default_ref_wav() -> Path | None:
    env_ref = _resolve_repo_path(os.getenv("HAKOCHUU_TTS_DEFAULT_REF_WAV", "").strip() or None)
    if env_ref is not None:
        return env_ref
    repo = _cosyvoice_repo_dir()
    if repo is not None:
        bundled = repo / "asset" / "zero_shot_prompt.wav"
        if bundled.is_file():
            return bundled
    return None


def _resample_pcm16_linear(pcm_bytes: bytes, src_rate: int, dst_rate: int = 24000) -> bytes:
    if src_rate == dst_rate or not pcm_bytes:
        return pcm_bytes
    count = len(pcm_bytes) // 2
    if count == 0:
        return b""
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


def _tensor_to_pcm16_24k(audio: Any, src_rate: int, dst_rate: int = 24000) -> bytes:
    """将 torch.Tensor / numpy.ndarray ([-1, 1] float32) 转换为 24kHz PCM16 字节。"""
    import numpy as np

    if hasattr(audio, "detach"):
        arr = audio.detach().cpu().float().numpy()
    else:
        arr = np.asarray(audio, dtype=np.float32)
    arr = np.squeeze(arr)
    if arr.ndim > 1:
        arr = arr[0]
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2").tobytes()
    return _resample_pcm16_linear(pcm, src_rate=src_rate, dst_rate=dst_rate)


def _patch_onnxruntime_providers() -> None:
    """CosyVoice frontend 会请求 CUDAExecutionProvider；CPU 版 onnxruntime 需回退。"""
    import onnxruntime as ort

    if getattr(ort, "_hakochuu_patched", False):
        return
    available = set(ort.get_available_providers())
    orig = ort.InferenceSession

    def _session(
        path: str,
        sess_options: Any = None,
        providers: Any = None,
        provider_options: Any = None,
        **kwargs: Any,
    ) -> Any:
        if providers:
            filtered = [p for p in providers if p in available]
            providers = filtered or ["CPUExecutionProvider"]
        return orig(
            path,
            sess_options=sess_options,
            providers=providers,
            provider_options=provider_options,
            **kwargs,
        )

    ort.InferenceSession = _session  # type: ignore[method-assign, assignment]
    ort._hakochuu_patched = True  # type: ignore[attr-defined]


def _prepare_cosyvoice_syspath() -> None:
    repo_dir = _cosyvoice_repo_dir()
    if repo_dir is None:
        return
    repo_s = str(repo_dir)
    if repo_s not in sys.path:
        sys.path.insert(0, repo_s)
    matcha_dir = str(repo_dir / "third_party" / "Matcha-TTS")
    if Path(matcha_dir).is_dir() and matcha_dir not in sys.path:
        sys.path.insert(0, matcha_dir)


def _cast_cosyvoice_qwen_to_float32(model: Any) -> None:
    """Qwen2Encoder 默认按 config 以 bfloat16 加载，与 CosyVoice float32 输入不匹配。"""
    import torch

    inner = getattr(getattr(getattr(model, "model", None), "llm", None), "llm", None)
    hf_model = getattr(inner, "model", None)
    if hf_model is not None:
        hf_model.to(dtype=torch.float32)


def _get_or_load_model() -> tuple[str, Any]:
    global _MODEL_INSTANCE, _MODEL_ENGINE, _WEIGHTS_DIR
    with _MODEL_LOCK:
        if _MODEL_INSTANCE is not None:
            return _MODEL_ENGINE, _MODEL_INSTANCE

        engine, weights_dir = _load_tts_manifest()
        _MODEL_ENGINE = engine
        _WEIGHTS_DIR = weights_dir

        if not weights_dir or not Path(weights_dir).exists():
            raise HTTPException(
                status_code=503,
                detail=f"TTS 权重目录不存在: {weights_dir or '(未配置)'}",
            )

        if engine == "cosyvoice3":
            t0 = time.perf_counter()
            _prepare_cosyvoice_syspath()
            _patch_onnxruntime_providers()
            try:
                from cosyvoice.cli.cosyvoice import AutoModel  # type: ignore[import-not-found]

                _MODEL_INSTANCE = AutoModel(model_dir=weights_dir)
                _cast_cosyvoice_qwen_to_float32(_MODEL_INSTANCE)
                logger.info(
                    "TTS 模型加载完成 engine=cosyvoice3 weights=%s elapsed_ms=%.1f",
                    weights_dir,
                    (time.perf_counter() - t0) * 1000,
                )
                return _MODEL_ENGINE, _MODEL_INSTANCE
            except ImportError:
                try:
                    from cosyvoice.cli.cosyvoice import (  # type: ignore[import-not-found]
                        CosyVoice2,
                    )

                    _MODEL_INSTANCE = CosyVoice2(weights_dir)
                    logger.info(
                        "TTS 模型加载完成 engine=cosyvoice2 weights=%s elapsed_ms=%.1f",
                        weights_dir,
                        (time.perf_counter() - t0) * 1000,
                    )
                    return _MODEL_ENGINE, _MODEL_INSTANCE
                except ImportError as exc:
                    logger.exception("CosyVoice 运行时导入失败")
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "服务端未安装 CosyVoice 运行时，请设置 HAKOCHUU_COSYVOICE_REPO_DIR "
                            "或在 .venv 中安装 cosyvoice 依赖"
                        ),
                    ) from exc

        if engine == "qwen3_tts":
            t0 = time.perf_counter()
            try:
                import torch
                from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]
            except ImportError as exc:
                logger.exception("qwen-tts 导入失败")
                raise HTTPException(
                    status_code=503,
                    detail="服务端未安装 qwen-tts，请先安装 Qwen3-TTS 依赖",
                ) from exc

            device = os.getenv("HAKOCHUU_TTS_DEVICE", "cuda:0")
            _MODEL_INSTANCE = Qwen3TTSModel.from_pretrained(
                weights_dir,
                device_map=device,
                dtype=torch.bfloat16,
            )
            logger.info(
                "TTS 模型加载完成 engine=qwen3_tts weights=%s device=%s elapsed_ms=%.1f",
                weights_dir,
                device,
                (time.perf_counter() - t0) * 1000,
            )
            return _MODEL_ENGINE, _MODEL_INSTANCE

        raise HTTPException(status_code=503, detail=f"不支持的 TTS 引擎: {engine}")


def _cv3_prompt_text(raw: str | None) -> str:
    text = (raw or "").strip() or os.getenv("HAKOCHUU_TTS_DEFAULT_REF_TEXT", "").strip()
    if not text:
        return DEFAULT_CV3_PROMPT_TEXT
    if "<|endofprompt|>" in text:
        return text
    return f"You are a helpful assistant.<|endofprompt|>{text}"


def _stream_cosyvoice(model: Any, req: TtsStreamRequest) -> Iterator[bytes]:
    src_rate = int(getattr(model, "sample_rate", 24000))
    ref_path = _resolve_repo_path(req.ref_speech_path) or _default_ref_wav()

    if ref_path is not None:
        if req.emotion and req.emotion != "neutral" and hasattr(model, "inference_instruct2"):
            instruct = _cv3_prompt_text(f"请用{req.emotion}的语气自然地说话。")
            gen = model.inference_instruct2(req.text, instruct, str(ref_path), stream=True)
        elif hasattr(model, "inference_zero_shot"):
            gen = model.inference_zero_shot(
                req.text, _cv3_prompt_text(req.ref_speech_text), str(ref_path), stream=True
            )
        else:
            gen = model.inference_cross_lingual(req.text, str(ref_path), stream=True)
    else:
        spks = model.list_available_spks() if hasattr(model, "list_available_spks") else []
        if not spks:
            raise HTTPException(
                status_code=400,
                detail="缺少角色引导参考音频 (ref_speech_path) 且模型无内置 SFT 说话人",
            )
        gen = model.inference_sft(req.text, spks[0], stream=True)

    for chunk in gen:
        speech = chunk.get("tts_speech") if isinstance(chunk, dict) else chunk
        if speech is not None:
            pcm = _tensor_to_pcm16_24k(speech, src_rate=src_rate, dst_rate=req.sample_rate)
            if pcm:
                yield pcm


def _stream_qwen3_tts(model: Any, req: TtsStreamRequest) -> Iterator[bytes]:
    ref_path = _resolve_repo_path(req.ref_speech_path)
    if ref_path is not None:
        wavs, sr = model.generate_voice_clone(
            text=req.text,
            language="Auto",
            ref_audio=str(ref_path),
            ref_text=req.ref_speech_text or "",
            x_vector_only_mode=not bool(req.ref_speech_text),
        )
    else:
        wavs, sr = model.generate_custom_voice(
            text=req.text,
            language="Auto",
            instruct=f"Emotion: {req.emotion}" if req.emotion != "neutral" else None,
        )
    if wavs:
        pcm = _tensor_to_pcm16_24k(wavs[0], src_rate=int(sr), dst_rate=req.sample_rate)
        for i in range(0, len(pcm), 960):
            yield pcm[i : i + 960]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    _get_or_load_model()
    yield


app = FastAPI(title="Hakochuu TTS Service", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    engine, weights_dir = _load_tts_manifest()
    repo = _cosyvoice_repo_dir()
    return {
        "status": "ok",
        "engine": engine,
        "weights_dir": weights_dir,
        "weights_exists": bool(weights_dir and Path(weights_dir).exists()),
        "repo_dir": str(repo) if repo is not None else "",
        "loaded": _MODEL_INSTANCE is not None,
    }


@app.post("/v1/tts/offload")
async def offload_model() -> dict[str, str]:
    global _MODEL_INSTANCE
    with _MODEL_LOCK:
        _MODEL_INSTANCE = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    logger.info("TTS 模型已卸载并清理 CUDA 缓存")
    return {"status": "offloaded"}


@app.post("/v1/tts/stream")
async def tts_stream(req: TtsStreamRequest) -> StreamingResponse:
    text = req.text.strip()
    if not text:
        return StreamingResponse(iter([b""]), media_type="application/octet-stream")

    engine, model = _get_or_load_model()
    t0 = time.perf_counter()

    async def _async_iter() -> Any:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=32)

        def _producer() -> None:
            try:
                it = (
                    _stream_cosyvoice(model, req)
                    if engine == "cosyvoice3"
                    else _stream_qwen3_tts(model, req)
                )
                for pcm in it:
                    asyncio.run_coroutine_threadsafe(queue.put(pcm), loop).result()
            except BaseException as exc:  # noqa: BLE001
                logger.exception("TTS 流式推理异常 character=%s", req.character_id)
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        threading.Thread(target=_producer, daemon=True).start()
        first_chunk = True
        total_bytes = 0
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            if first_chunk:
                first_chunk = False
                logger.info(
                    "TTS 首包生成 engine=%s char=%s emotion=%s first_chunk_ms=%.1f",
                    engine,
                    req.character_id,
                    req.emotion,
                    (time.perf_counter() - t0) * 1000,
                )
            total_bytes += len(item)
            yield item
        logger.info(
            "TTS 合成完成 engine=%s char=%s bytes=%d elapsed_ms=%.1f",
            engine,
            req.character_id,
            total_bytes,
            (time.perf_counter() - t0) * 1000,
        )

    return StreamingResponse(_async_iter(), media_type="application/octet-stream")
