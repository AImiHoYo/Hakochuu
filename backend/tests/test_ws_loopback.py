from fastapi.testclient import TestClient

from app.main import app
from app.ws.protocol import BinaryFrame, CodecId, FrameFlags, FrameType

client = TestClient(app)


def test_system_capabilities() -> None:
    resp = client.get("/v1/system/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["protocol_version"] == "1.0"
    assert "pcm16_24k" in data["supported_codecs"]


def test_ws_session_16b_loopback_and_barge_in() -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_json(
            {
                "type": "HELLO",
                "protocol_version": "1.0",
                "deploy_mode": "local",
                "capabilities": ["wasm_vad", "client_mixer"],
                "character_id": "char_001",
            }
        )
        ack = ws.receive_json()
        assert ack["type"] == "HELLO_ACK"
        assert ack["negotiated_codec"] == int(CodecId.PCM16_24K)

        # 1. 发送正常 16-Byte 二进制帧并校验回环
        up_frame = BinaryFrame(
            frame_type=FrameType.USER_AUDIO_UP,
            codec=CodecId.PCM16_24K,
            gen_id=1,
            pts_ms=20,
            payload=b"hello-pcm",
            flags=FrameFlags.KEYFRAME,
        )
        ws.send_bytes(up_frame.pack())
        down_raw = ws.receive_bytes()
        down_frame = BinaryFrame.unpack(down_raw)
        assert down_frame.frame_type == FrameType.TTS_AUDIO_DOWN
        assert down_frame.gen_id == 1
        assert down_frame.pts_ms == 20
        assert down_frame.payload == b"hello-pcm"

        # 2. 发送 INTERRUPT 将 active_gen_id 推进到 5
        ws.send_json({"type": "INTERRUPT", "gen_id": 5})
        int_ack = ws.receive_json()
        assert int_ack["type"] == "INTERRUPT_ACK"
        assert int_ack["active_gen_id"] == 5

        # 3. 发送旧 gen_id=3 滞留帧（应被服务端丢弃），随后发送 gen_id=5 帧（应正常回环）
        stale_frame = BinaryFrame(
            frame_type=FrameType.USER_AUDIO_UP,
            codec=CodecId.PCM16_24K,
            gen_id=3,
            pts_ms=40,
            payload=b"stale",
        )
        ws.send_bytes(stale_frame.pack())

        fresh_frame = BinaryFrame(
            frame_type=FrameType.USER_AUDIO_UP,
            codec=CodecId.PCM16_24K,
            gen_id=5,
            pts_ms=60,
            payload=b"fresh",
        )
        ws.send_bytes(fresh_frame.pack())
        res = BinaryFrame.unpack(ws.receive_bytes())
        assert res.gen_id == 5
        assert res.payload == b"fresh"
