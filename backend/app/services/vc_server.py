"""独立零样本 VC 翻唱微服务（默认监听 23335 端口，运行于 GPU 服务端）。

仅输出纯人声干声流（24kHz 单声道 PCM16，每 20ms 对应 960 字节），严禁混入伴奏，
由前端 DualTrackMixer 按 PTS 毫秒时间戳与强缓存伴奏轨精确混音。
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

logger = setup_logging("vc")

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "shared" / "models_manifest.yaml"
FRAME_BYTES_24K_20MS = 960

_VC_INSTANCE: Any = None
_VC_ENGINE: str = "seed_vc"
_WEIGHTS_DIR: str = ""
_VC_LOCK = threading.Lock()


class VcStreamRequest(BaseModel):
    song_id: str
    character_id: str = "char_001"
    key_shift: int = 0
    ref_sing_path: str | None = None
    sample_rate: int = 24000


def _load_vc_manifest() -> tuple[str, str]:
    engine = os.getenv("HAKOCHUU_VC_ENGINE", "").strip()
    weights_dir = os.getenv("HAKOCHUU_VC_WEIGHTS_DIR", "").strip()
    if MANIFEST_PATH.is_file():
        data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
        vc_cfg = (data.get("models") or {}).get("vc") or {}
        if not engine:
            engine = str(vc_cfg.get("engine") or "seed_vc")
        if not weights_dir:
            weights_dir = str(vc_cfg.get("weights_dir") or "")
        cache_cfg = data.get("cache") or {}
        if cache_cfg.get("hf_home"):
            os.environ.setdefault("HF_HOME", str((REPO_ROOT / cache_cfg["hf_home"]).resolve()))
        if cache_cfg.get("torch_home"):
            os.environ.setdefault(
                "TORCH_HOME", str((REPO_ROOT / cache_cfg["torch_home"]).resolve())
            )
    return engine or "seed_vc", weights_dir


def _vc_repo_dir() -> Path | None:
    raw = os.getenv("HAKOCHUU_VC_REPO_DIR", "").strip()
    if not raw:
        candidate = Path("/path/to/projects/seed-vc")
        return candidate if candidate.is_dir() else None
    p = Path(raw)
    return p if p.is_dir() else None


def _resolve_repo_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_file():
        return p
    for base in (REPO_ROOT, REPO_ROOT / "backend", REPO_ROOT / "storage"):
        cand = (base / raw_path).resolve()
        if cand.is_file():
            return cand
    return None


def _resolve_song_lead_vocal(song_id: str) -> Path | None:
    storage_dir = Path(os.getenv("HAKOCHUU_STORAGE_DIR", str(REPO_ROOT / "storage"))).resolve()
    for base in (storage_dir, REPO_ROOT / "storage"):
        cand = (base / "songs" / song_id / "lead_vocal.wav").resolve()
        if cand.is_file():
            return cand
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


def _prepare_seed_vc_syspath() -> Path | None:
    repo_dir = _vc_repo_dir()
    if repo_dir is None:
        return None
    repo_s = str(repo_dir)
    if repo_s not in sys.path:
        sys.path.insert(0, repo_s)
    return repo_dir


def _stub_dac_vector_quantize() -> None:
    """DiT 翻唱配置 vector_quantize=false，避免为 dac 降级 protobuf 破坏 vLLM。"""
    if "dac.nn.quantize" in sys.modules:
        return
    try:
        from dac.nn.quantize import VectorQuantize  # noqa: F401
        return
    except Exception:
        pass

    import types

    import torch.nn as nn

    dac = types.ModuleType("dac")
    dac_nn = types.ModuleType("dac.nn")
    dac_nn_q = types.ModuleType("dac.nn.quantize")

    class VectorQuantize(nn.Module):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__()
            raise RuntimeError("当前 Seed-VC 配置未启用 VectorQuantize")

    dac_nn_q.VectorQuantize = VectorQuantize
    sys.modules["dac"] = dac
    sys.modules["dac.nn"] = dac_nn
    sys.modules["dac.nn.quantize"] = dac_nn_q


def _patch_seed_vc_hf_cache(weights_dir: str) -> None:
    """把 Seed-VC 的 hf_hub_download 缓存指到 weights_dir，避免落到 backend/checkpoints。"""
    from huggingface_hub import hf_hub_download

    cache_dir = str(Path(weights_dir).resolve())
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    def _load_custom_model_from_hf(
        repo_id: str,
        model_filename: str = "pytorch_model.bin",
        config_filename: str | None = None,
    ) -> Any:
        model_path = hf_hub_download(
            repo_id=repo_id, filename=model_filename, cache_dir=cache_dir
        )
        if not config_filename:
            return model_path
        config_path = hf_hub_download(
            repo_id=repo_id, filename=config_filename, cache_dir=cache_dir
        )
        return model_path, config_path

    import hf_utils  # type: ignore[import-not-found]

    hf_utils.load_custom_model_from_hf = _load_custom_model_from_hf
    if "seed_vc_wrapper" in sys.modules:
        sys.modules["seed_vc_wrapper"].load_custom_model_from_hf = _load_custom_model_from_hf


def _patch_bigvgan_hub_mixin() -> None:
    """兼容 huggingface_hub 2.x：PyTorchModelHubMixin 不再传入 proxies/resume_download。"""
    from modules.bigvgan import bigvgan  # type: ignore[import-not-found]

    raw = bigvgan.BigVGAN.__dict__["_from_pretrained"]
    orig_fn = raw.__func__ if isinstance(raw, classmethod) else raw

    @classmethod
    def _from_pretrained(cls, **kwargs: Any):  # noqa: ANN401
        kwargs.setdefault("proxies", None)
        kwargs.setdefault("resume_download", False)
        kwargs.setdefault("revision", None)
        kwargs.setdefault("cache_dir", None)
        kwargs.setdefault("force_download", False)
        kwargs.setdefault("local_files_only", False)
        kwargs.setdefault("token", None)
        return orig_fn(cls, **kwargs)

    bigvgan.BigVGAN._from_pretrained = _from_pretrained


def _unpack_audio_pair(value: Any, default_sr: int) -> tuple[int, Any] | None:
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2:
        a, b = value
        if isinstance(a, int) and b is not None:
            return int(a), b
        if isinstance(b, int) and a is not None:
            return int(b), a
        nested = _unpack_audio_pair(b, default_sr)
        if nested is not None:
            return nested
        nested = _unpack_audio_pair(a, default_sr)
        if nested is not None:
            return nested
        return None
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return default_sr, value
    except Exception:
        pass
    if hasattr(value, "detach") or hasattr(value, "shape"):
        return default_sr, value
    return None


def _collect_convert_output(result: Any, default_sr: int) -> tuple[int, Any]:
    """兼容 SeedVCWrapper.convert_voice 生成器与 (sr, wav) / ndarray 返回值。"""
    if result is None:
        raise RuntimeError("Seed-VC 未产出音频")

    unpacked = _unpack_audio_pair(result, default_sr)
    if unpacked is not None:
        return unpacked

    try:
        import numpy as np

        if isinstance(result, np.ndarray):
            return default_sr, result
    except Exception:
        pass

    if hasattr(result, "__iter__") and not isinstance(result, (bytes, bytearray, tuple, list)):
        last_audio: tuple[int, Any] | None = None
        for item in result:
            unpacked = _unpack_audio_pair(item, default_sr)
            if unpacked is not None:
                last_audio = unpacked
        if last_audio is None:
            raise RuntimeError("Seed-VC 流式转换未产出音频")
        return last_audio

    raise RuntimeError(f"无法解析 Seed-VC 输出: {type(result)!r}")


def _get_or_load_vc() -> tuple[str, Any]:
    global _VC_INSTANCE, _VC_ENGINE, _WEIGHTS_DIR
    with _VC_LOCK:
        if _VC_INSTANCE is not None:
            return _VC_ENGINE, _VC_INSTANCE

        engine, weights_dir = _load_vc_manifest()
        _VC_ENGINE = engine
        _WEIGHTS_DIR = weights_dir

        if not weights_dir:
            raise HTTPException(status_code=503, detail="未配置 Seed-VC 权重目录 HAKOCHUU_VC_WEIGHTS_DIR")
        Path(weights_dir).mkdir(parents=True, exist_ok=True)

        repo_dir = _prepare_seed_vc_syspath()
        if repo_dir is None:
            raise HTTPException(
                status_code=503,
                detail="服务端未找到 Seed-VC 源码目录，请设置 HAKOCHUU_VC_REPO_DIR",
            )

        _stub_dac_vector_quantize()
        _patch_seed_vc_hf_cache(weights_dir)

        t0 = time.perf_counter()
        try:
            import torch
            from seed_vc_wrapper import SeedVCWrapper  # type: ignore[import-not-found]
        except ImportError as exc:
            logger.exception("Seed-VC 运行时导入失败")
            raise HTTPException(
                status_code=503,
                detail=(
                    "服务端未安装 Seed-VC 运行时，请设置 HAKOCHUU_VC_REPO_DIR "
                    "并确保 .venv 中已安装 munch / pydub / librosa"
                ),
            ) from exc

        _patch_bigvgan_hub_mixin()
        device_name = os.getenv("HAKOCHUU_VC_DEVICE", "cuda:0").strip() or "cuda:0"
        device = torch.device(device_name if torch.cuda.is_available() else "cpu")
        _VC_INSTANCE = SeedVCWrapper(device=device)
        logger.info(
            "Seed-VC 模型加载完成 weights=%s device=%s elapsed_ms=%.1f",
            weights_dir,
            device,
            (time.perf_counter() - t0) * 1000,
        )
        return _VC_ENGINE, _VC_INSTANCE


def _stream_seed_vc(model: Any, req: VcStreamRequest) -> Iterator[bytes]:
    lead_vocal = _resolve_song_lead_vocal(req.song_id)
    if lead_vocal is None:
        raise HTTPException(
            status_code=404,
            detail=f"曲库歌曲 {req.song_id} 缺少人声干声 lead_vocal.wav",
        )
    ref_sing = _resolve_repo_path(req.ref_sing_path)
    if ref_sing is None:
        default_ref = os.getenv("HAKOCHUU_VC_DEFAULT_REF_WAV", "").strip()
        ref_sing = _resolve_repo_path(default_ref) if default_ref else None
    if ref_sing is None:
        raise HTTPException(status_code=400, detail="缺少角色清唱参考音频 (ref_sing_path)")

    diffusion_steps = int(os.getenv("HAKOCHUU_VC_DIFFUSION_STEPS", "30"))
    default_sr = int(getattr(model, "sr_f0", getattr(model, "sr", 44100)))

    if hasattr(model, "convert_voice"):
        raw = model.convert_voice(
            source=str(lead_vocal),
            target=str(ref_sing),
            diffusion_steps=diffusion_steps,
            length_adjust=1.0,
            inference_cfg_rate=0.7,
            f0_condition=True,
            auto_f0_adjust=False,
            pitch_shift=req.key_shift,
            stream_output=True,
        )
    elif hasattr(model, "convert"):
        raw = model.convert(
            source=str(lead_vocal),
            target=str(ref_sing),
            f0_condition=True,
            semi_tone_shift=req.key_shift,
        )
    else:
        raise HTTPException(status_code=503, detail="Seed-VC 实例缺少 convert_voice / convert 接口")

    sr, wav = _collect_convert_output(raw, default_sr)
    pcm = _tensor_to_pcm16_24k(wav, src_rate=int(sr), dst_rate=req.sample_rate)
    for i in range(0, len(pcm), FRAME_BYTES_24K_20MS):
        chunk = pcm[i : i + FRAME_BYTES_24K_20MS]
        if chunk:
            yield chunk


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    _get_or_load_vc()
    yield


app = FastAPI(title="Hakochuu Seed-VC Service", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    engine, weights_dir = _load_vc_manifest()
    repo = _vc_repo_dir()
    return {
        "status": "ok",
        "engine": engine,
        "weights_dir": weights_dir,
        "weights_exists": bool(weights_dir and Path(weights_dir).exists()),
        "repo_dir": str(repo) if repo is not None else "",
        "loaded": _VC_INSTANCE is not None,
    }


@app.post("/v1/vc/offload")
async def offload_model() -> dict[str, str]:
    global _VC_INSTANCE
    with _VC_LOCK:
        _VC_INSTANCE = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    logger.info("Seed-VC 模型已卸载并清理 CUDA 缓存")
    return {"status": "offloaded"}


@app.post("/v1/vc/stream")
async def vc_stream(req: VcStreamRequest) -> StreamingResponse:
    _, model = _get_or_load_vc()
    t0 = time.perf_counter()

    async def _async_iter() -> Any:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=32)

        def _producer() -> None:
            try:
                for pcm in _stream_seed_vc(model, req):
                    asyncio.run_coroutine_threadsafe(queue.put(pcm), loop).result()
            except BaseException as exc:  # noqa: BLE001
                logger.exception("VC 流式转换异常 song_id=%s", req.song_id)
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
                    "VC 首包生成 song_id=%s char=%s key_shift=%d first_chunk_ms=%.1f",
                    req.song_id,
                    req.character_id,
                    req.key_shift,
                    (time.perf_counter() - t0) * 1000,
                )
            total_bytes += len(item)
            yield item
        logger.info(
            "VC 转换完成 song_id=%s char=%s bytes=%d elapsed_ms=%.1f",
            req.song_id,
            req.character_id,
            total_bytes,
            (time.perf_counter() - t0) * 1000,
        )

    return StreamingResponse(_async_iter(), media_type="application/octet-stream")
