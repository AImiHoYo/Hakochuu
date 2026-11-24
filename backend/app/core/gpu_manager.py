import asyncio
import os
import time
from dataclasses import dataclass
from typing import Literal

from app.core.logging import get_logger
from app.engines.base import BaseAudioEngine

logger = get_logger(__name__)

GpuMode = Literal["idle", "chat", "sing"]

BASE_RESIDENT_VRAM_MB = int(os.getenv("HAKOCHUU_BASE_VRAM_MB", "10500"))


@dataclass(slots=True)
class GpuStatus:
    active_mode: GpuMode
    vram_limit_mb: int
    estimated_used_mb: int
    tts_location: str
    vc_location: str


class GPUManager:
    """单卡 24GB 显存互斥调度器（对话常驻、唱歌错峰 + CPU Pinned Memory 热切换）。"""

    def __init__(self, vram_limit_mb: int | None = None) -> None:
        self.vram_limit_mb = vram_limit_mb or int(os.getenv("HAKOCHUU_VRAM_LIMIT_MB", "24576"))
        self._lock = asyncio.Lock()
        self._active_mode: GpuMode = "idle"
        self._tts_engine: BaseAudioEngine | None = None
        self._vc_engine: BaseAudioEngine | None = None

    def register_engines(
        self,
        *,
        tts_engine: BaseAudioEngine | None = None,
        vc_engine: BaseAudioEngine | None = None,
    ) -> None:
        if tts_engine is not None:
            self._tts_engine = tts_engine
        if vc_engine is not None:
            self._vc_engine = vc_engine

    @property
    def active_mode(self) -> GpuMode:
        return self._active_mode

    async def ensure_mode(self, target_mode: Literal["chat", "sing"]) -> GpuStatus:
        """确保目标模式所需引擎已驻留显存，并先将互斥引擎换出至 Pinned Memory。"""
        async with self._lock:
            if self._active_mode == target_mode:
                return self.get_status()

            prev_mode = self._active_mode
            t0 = time.perf_counter()
            if target_mode == "chat":
                if self._vc_engine and self._vc_engine.location == "gpu":
                    logger.info("GPU 显存切换: 卸载 VC 引擎至 Pinned CPU")
                    await self._vc_engine.offload_to_pinned_cpu()
                if self._tts_engine and self._tts_engine.location != "gpu":
                    logger.info("GPU 显存切换: 加载 TTS 引擎至 GPU")
                    await self._tts_engine.load_to_gpu()
            elif target_mode == "sing":
                if self._tts_engine and self._tts_engine.location == "gpu":
                    logger.info("GPU 显存切换: 卸载 TTS 引擎至 Pinned CPU")
                    await self._tts_engine.offload_to_pinned_cpu()
                if self._vc_engine and self._vc_engine.location != "gpu":
                    logger.info("GPU 显存切换: 加载 VC 引擎至 GPU")
                    await self._vc_engine.load_to_gpu()

            self._active_mode = target_mode
            status = self.get_status()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.info(
                "GPU 模式切换完成: %s -> %s (%.1fms, 显存估算 %d/%d MB, tts=%s, vc=%s)",
                prev_mode,
                target_mode,
                elapsed_ms,
                status.estimated_used_mb,
                status.vram_limit_mb,
                status.tts_location,
                status.vc_location,
            )
            if status.estimated_used_mb > self.vram_limit_mb:
                logger.error(
                    "显存预算超限: %d MB > %d MB",
                    status.estimated_used_mb,
                    self.vram_limit_mb,
                )
                raise RuntimeError(
                    f"显存预算超限: {status.estimated_used_mb} MB > {self.vram_limit_mb} MB"
                )
            return status

    def get_status(self) -> GpuStatus:
        used = BASE_RESIDENT_VRAM_MB
        if self._tts_engine and self._tts_engine.location == "gpu":
            used += self._tts_engine.estimated_vram_mb
        if self._vc_engine and self._vc_engine.location == "gpu":
            used += self._vc_engine.estimated_vram_mb

        return GpuStatus(
            active_mode=self._active_mode,
            vram_limit_mb=self.vram_limit_mb,
            estimated_used_mb=used,
            tts_location=self._tts_engine.location if self._tts_engine else "unregistered",
            vc_location=self._vc_engine.location if self._vc_engine else "unregistered",
        )


_GLOBAL_GPU_MANAGER: GPUManager | None = None


def get_gpu_manager() -> GPUManager:
    """获取进程级共享 GPUManager 单例，跨 WebSocket 会话协调 TTS/VC 显存互斥。"""
    global _GLOBAL_GPU_MANAGER
    if _GLOBAL_GPU_MANAGER is None:
        _GLOBAL_GPU_MANAGER = GPUManager()
    return _GLOBAL_GPU_MANAGER
