import httpx

from app.core.pipeline import RealtimePipeline
from app.engines.llm_router import LlmRouter
from app.engines.tts_engine import TtsEngine
from app.engines.vc_engine import VcEngine
from app.ws.protocol import FrameType


async def test_pipeline_chat_and_sing_mode_switch() -> None:
    pipeline = RealtimePipeline()

    # 1. 对话分支：应切换为 chat 模式并下发 TTS_AUDIO_DOWN (0x02)
    chat_outputs = [
        out
        async for out in pipeline.process_text_turn(
            user_text="你好呀",
            character_id="char_001",
            gen_id=1,
        )
    ]
    signals = [o.signal for o in chat_outputs if o.signal]
    frames = [o.binary_frame for o in chat_outputs if o.binary_frame]

    assert any(s["type"] == "EMOTION_TAG" for s in signals)
    assert any(s["type"] == "LLM_TEXT" for s in signals)
    assert len(frames) > 0
    assert all(f.frame_type == FrameType.TTS_AUDIO_DOWN for f in frames)
    assert pipeline.gpu_manager.active_mode == "chat"

    # 2. 翻唱分支：应切为 sing 模式（TTS 换出至 pinned_cpu），下发 VC_VOCAL_DOWN (0x03)
    sing_outputs = [
        out
        async for out in pipeline.process_text_turn(
            user_text="帮我唱一首歌",
            character_id="char_001",
            gen_id=2,
        )
    ]
    sing_signals = [o.signal for o in sing_outputs if o.signal]
    vc_frames = [
        o.binary_frame
        for o in sing_outputs
        if o.binary_frame and o.binary_frame.frame_type == FrameType.VC_VOCAL_DOWN
    ]

    assert any(s["type"] == "SING_START" for s in sing_signals)
    assert len(vc_frames) == 10
    assert vc_frames[0].pts_ms == 0
    assert vc_frames[1].pts_ms == 20
    assert pipeline.gpu_manager.active_mode == "sing"
    assert pipeline.tts_engine.location == "pinned_cpu"
    assert pipeline.vc_engine.location == "gpu"


async def test_pipeline_barge_in_cancellation() -> None:
    pipeline = RealtimePipeline()
    collected = []
    async for out in pipeline.process_text_turn(
        user_text="这是一段较长的对话测试文本",
        character_id="char_001",
        gen_id=3,
        is_cancelled=lambda _gid: len(collected) >= 2,
    ):
        collected.append(out)

    assert len(collected) == 2


async def test_pipeline_missing_tts_and_vc_degrades_gracefully() -> None:
    class FailingTtsEngine(TtsEngine):
        async def synthesize_stream(self, text: str, character_id: str, emotion: str = "neutral"):
            raise httpx.ConnectError("Connection refused")
            yield b""

    class FailingVcEngine(VcEngine):
        async def convert_song_stream(self, song_id: str, character_id: str, key_shift: int = 0):
            raise httpx.ConnectError("Connection refused")
            yield 0, b""

    pipeline = RealtimePipeline(
        llm_router=LlmRouter(mock_mode=True),
        tts_engine=FailingTtsEngine(mock_mode=False),
        vc_engine=FailingVcEngine(mock_mode=False),
    )

    chat_outputs = [
        out
        async for out in pipeline.process_text_turn("今天天气如何", "char_001", gen_id=10)
    ]
    chat_signals = [o.signal for o in chat_outputs if o.signal]
    assert any(s["type"] == "LLM_TEXT" for s in chat_signals)
    assert any(
        s["type"] == "DEBUG_WARNING" and s["component"] == "TTS" for s in chat_signals
    )
    assert chat_signals[-1]["type"] == "TURN_DONE"

    sing_outputs = [
        out
        async for out in pipeline.process_text_turn("帮我唱一首晴天", "char_001", gen_id=11)
    ]
    sing_signals = [o.signal for o in sing_outputs if o.signal]
    assert any(
        s["type"] == "DEBUG_WARNING" and s["component"] == "VC" for s in sing_signals
    )
    assert sing_signals[-1]["type"] == "TURN_DONE"
