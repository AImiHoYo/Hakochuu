from abc import ABC, abstractmethod
from typing import Literal

EngineLocation = Literal["unloaded", "pinned_cpu", "gpu"]


class BaseAudioEngine(ABC):
    """TTS / VC 等重型推理引擎基类，统一由 GPUManager 管理显存与 Pinned Memory 切换。"""

    name: str
    estimated_vram_mb: int
    location: EngineLocation = "unloaded"

    @abstractmethod
    async def load_to_gpu(self) -> None:
        """从磁盘或 CPU 锁页内存加载权重到显存。"""

    @abstractmethod
    async def offload_to_pinned_cpu(self) -> None:
        """将权重从显存换出至 CPU Pinned Memory，释放 VRAM 槽位。"""
