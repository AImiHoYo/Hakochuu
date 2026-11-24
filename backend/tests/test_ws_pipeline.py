import json

from fastapi.testclient import TestClient

from app.main import app
from app.ws.protocol import BinaryFrame, CodecId, FrameFlags, FrameType

client = TestClient(app)


def _collect_until_turn_done(ws) -> tuple[list[dict], list[BinaryFrame]]:
    signals: list[dict] = []
    frames: list[BinaryFrame] = []
    while True:
        msg = ws.receive()
        if "text" in msg and msg["text"] is not None:
            data = json.loads(msg["text"])
            signals.append(data)
            if data.get("type") == "TURN_DONE":
                break
        elif "bytes" in msg and msg["bytes"] is not None:
            frames.append(BinaryFrame.unpack(msg["bytes"]))
    return signals, frames


def test_ws_text_turn_chat_and_sing() -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_json({"type": "HELLO", "deploy_mode": "local", "character_id": "char_001"})
        assert ws.receive_json()["type"] == "HELLO_ACK"

        # 1. 文本触发对话流式 TTS (0x02)
        ws.send_json({"type": "TEXT_TURN", "gen_id": 1, "text": "你好呀"})
        signals, frames = _collect_until_turn_done(ws)
        assert any(s["type"] == "EMOTION_TAG" for s in signals)
        assert len(frames) > 0
        assert all(f.frame_type == FrameType.TTS_AUDIO_DOWN for f in frames)

        # 2. 文本触发唱歌意图 (0x03)
        ws.send_json({"type": "TEXT_TURN", "gen_id": 2, "text": "帮我唱一首晴天"})
        sing_signals, sing_frames = _collect_until_turn_done(ws)
        assert any(s["type"] == "SING_START" for s in sing_signals)
        assert any(f.frame_type == FrameType.VC_VOCAL_DOWN for f in sing_frames)


def test_ws_voice_eos_triggers_asr_and_pipeline() -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_json({"type": "HELLO", "deploy_mode": "local", "character_id": "char_001"})
        assert ws.receive_json()["type"] == "HELLO_ACK"

        # 发送带 EOS 标志的语音帧触发 ASR + Pipeline
        eos_frame = BinaryFrame(
            frame_type=FrameType.USER_AUDIO_UP,
            codec=CodecId.PCM16_24K,
            gen_id=1,
            pts_ms=0,
            payload=b"\x00\x01" * 480,
            flags=FrameFlags.EOS,
        )
        ws.send_bytes(eos_frame.pack())
        signals, frames = _collect_until_turn_done(ws)
        assert any(s["type"] == "ASR_RESULT" and s["text"] for s in signals)
        assert any(f.frame_type == FrameType.TTS_AUDIO_DOWN for f in frames)
