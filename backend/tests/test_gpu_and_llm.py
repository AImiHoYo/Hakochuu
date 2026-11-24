from app.core.gpu_manager import GPUManager
from app.engines.base import BaseAudioEngine
from app.engines.llm_router import LlmRouter, strip_emotion_tags


class DummyEngine(BaseAudioEngine):
    def __init__(self, name: str, vram_mb: int) -> None:
        self.name = name
        self.estimated_vram_mb = vram_mb

    async def load_to_gpu(self) -> None:
        self.location = "gpu"

    async def offload_to_pinned_cpu(self) -> None:
        self.location = "pinned_cpu"


async def test_gpu_manager_mutual_exclusion() -> None:
    tts = DummyEngine("cosyvoice3", 4500)
    vc = DummyEngine("seed_vc", 5000)
    manager = GPUManager(vram_limit_mb=24576)
    manager.register_engines(tts_engine=tts, vc_engine=vc)

    chat_status = await manager.ensure_mode("chat")
    assert chat_status.active_mode == "chat"
    assert chat_status.tts_location == "gpu"
    assert chat_status.estimated_used_mb == 15000

    sing_status = await manager.ensure_mode("sing")
    assert sing_status.active_mode == "sing"
    assert sing_status.tts_location == "pinned_cpu"
    assert sing_status.vc_location == "gpu"
    assert sing_status.estimated_used_mb == 15500

    back_status = await manager.ensure_mode("chat")
    assert back_status.vc_location == "pinned_cpu"
    assert back_status.tts_location == "gpu"


def test_strip_emotion_tags() -> None:
    cleaned, tags = strip_emotion_tags("[emotion:cheerful][action:nod]你好，欢迎回来！")
    assert cleaned == "你好，欢迎回来！"
    assert tags == [("emotion", "cheerful"), ("action", "nod")]


async def test_llm_router_mock_singing_intent() -> None:
    router = LlmRouter(mock_mode=True)
    events = [ev async for ev in router.stream_chat("帮我翻唱一首晴天")]
    kinds = [ev.kind for ev in events]
    assert "emotion" in kinds
    assert "sing_intent" in kinds
    assert kinds[-1] == "done"
