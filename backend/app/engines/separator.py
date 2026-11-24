"""离线曲库人声/伴奏分离引擎（BS-RoFormer）。

输出目录：storage/songs/<song_id>/
  - lead_vocal.wav      (供 Seed-VC 转换的纯人声干声)
  - instrumental.opus   (或回退 instrumental.wav，供前端 206 Range 强缓存播放)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.core.logging import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "shared" / "models_manifest.yaml"


@dataclass(slots=True)
class SeparationResult:
    song_id: str
    lead_vocal_path: Path
    instrumental_path: Path
    duration_ms: int


def _load_separator_config() -> tuple[str, str, str]:
    weights_dir = os.getenv("HAKOCHUU_SEPARATOR_WEIGHTS_DIR", "").strip()
    model_filename = os.getenv("HAKOCHUU_SEPARATOR_MODEL", "").strip()
    cache_dir = ""
    if MANIFEST_PATH.is_file():
        data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
        sep_cfg = (data.get("models") or {}).get("separator") or {}
        if not weights_dir:
            weights_dir = str(sep_cfg.get("weights_dir") or "")
        if not model_filename:
            model_filename = str(
                sep_cfg.get("model_filename") or "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
            )
        cache_cfg = data.get("cache") or {}
        if cache_cfg.get("audio_separator_models"):
            cache_dir = str((REPO_ROOT / cache_cfg["audio_separator_models"]).resolve())
    return (
        weights_dir or "/path/to/pretrain_models/separator",
        model_filename or "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        cache_dir or str((REPO_ROOT / "cache" / "audio_separator").resolve()),
    )


def _probe_duration_ms(audio_path: Path) -> int:
    try:
        import wave

        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return int(frames / rate * 1000) if rate else 0
    except Exception:
        return 0


def separate_song_sync(
    input_audio: Path,
    song_id: str,
    output_root: Path,
    on_progress: Callable[[int, str], None] | None = None,
) -> SeparationResult:
    """使用 python-audio-separator (BS-RoFormer) 同步分离人声与伴奏。"""
    def _report(pct: int, stage: str) -> None:
        if on_progress is not None:
            on_progress(pct, stage)

    try:
        from audio_separator.separator import Separator  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "未安装 audio-separator，请先在服务端安装 python-audio-separator 依赖"
        ) from exc

    t0 = time.perf_counter()
    weights_dir, model_filename, cache_dir = _load_separator_config()
    song_dir = (output_root / "songs" / song_id).resolve()
    song_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = song_dir / "_sep_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    model_dir = weights_dir if Path(weights_dir).is_dir() else cache_dir
    Path(model_dir).mkdir(parents=True, exist_ok=True)

    _report(15, "正在加载 BS-RoFormer 模型")
    logger.info("开始曲库分离 song_id=%s model=%s input=%s", song_id, model_filename, input_audio.name)
    separator = Separator(
        output_dir=str(tmp_dir),
        output_format="WAV",
        model_file_dir=model_dir,
    )
    separator.load_model(model_filename=model_filename)

    _report(35, "正在运行 BS-RoFormer 人声/伴奏分离")
    outputs = separator.separate(str(input_audio))

    vocal_src: Path | None = None
    inst_src: Path | None = None
    for out_name in outputs:
        p = Path(out_name)
        if not p.is_absolute():
            p = tmp_dir / p
        low = p.name.lower()
        if "vocal" in low and "instrumental" not in low:
            vocal_src = p
        elif "instrumental" in low or "no_vocals" in low:
            inst_src = p

    if vocal_src is None or inst_src is None or not vocal_src.is_file() or not inst_src.is_file():
        raise RuntimeError(f"BS-RoFormer 分离输出不完整: {outputs}")

    lead_vocal_path = song_dir / "lead_vocal.wav"
    shutil.move(str(vocal_src), str(lead_vocal_path))

    _report(80, "正在转码伴奏为 Opus")
    # 优先转码为 Opus 伴奏以减小体积，无 ffmpeg 时保留 WAV
    inst_opus = song_dir / "instrumental.opus"
    inst_wav = song_dir / "instrumental.wav"
    if shutil.which("ffmpeg"):
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(inst_src),
                "-c:a",
                "libopus",
                "-b:a",
                "128k",
                str(inst_opus),
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0 and inst_opus.is_file():
            inst_src.unlink(missing_ok=True)
            final_inst = inst_opus
        else:
            shutil.move(str(inst_src), str(inst_wav))
            final_inst = inst_wav
    else:
        shutil.move(str(inst_src), str(inst_wav))
        final_inst = inst_wav

    shutil.rmtree(tmp_dir, ignore_errors=True)
    duration_ms = _probe_duration_ms(lead_vocal_path)
    _report(95, "分离完成，正在写入曲库")
    logger.info(
        "曲库分离完成 song_id=%s duration_ms=%d elapsed_ms=%.1f",
        song_id,
        duration_ms,
        (time.perf_counter() - t0) * 1000,
    )
    return SeparationResult(
        song_id=song_id,
        lead_vocal_path=lead_vocal_path,
        instrumental_path=final_inst,
        duration_ms=duration_ms,
    )
