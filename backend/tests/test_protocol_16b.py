from app.db.models import CharacterProfile, FinetuneJob, SongAsset
from app.ws.protocol import (
    HEADER_SIZE,
    BinaryFrame,
    CodecId,
    FrameFlags,
    FrameType,
)


def test_pack_and_unpack_roundtrip() -> None:
    payload = b"\x01\x02\x03\x04\x05\x06"
    frame = BinaryFrame(
        frame_type=FrameType.TTS_AUDIO_DOWN,
        codec=CodecId.OPUS_24K,
        gen_id=42,
        pts_ms=123456,
        payload=payload,
        flags=FrameFlags.EOS | FrameFlags.KEYFRAME,
    )

    raw = frame.pack()
    assert len(raw) == HEADER_SIZE + len(payload)

    decoded = BinaryFrame.unpack(raw)
    assert decoded.version == 1
    assert decoded.flags == (FrameFlags.EOS | FrameFlags.KEYFRAME)
    assert decoded.frame_type == FrameType.TTS_AUDIO_DOWN
    assert decoded.codec == CodecId.OPUS_24K
    assert decoded.gen_id == 42
    assert decoded.pts_ms == 123456
    assert decoded.payload == payload


def test_big_endian_header_layout() -> None:
    frame = BinaryFrame(
        frame_type=FrameType.VC_VOCAL_DOWN,
        codec=CodecId.PCM16_24K,
        gen_id=1,
        pts_ms=1000,
        payload=b"abcd",
    )
    raw = frame.pack()
    # Byte 0: version=1, flags=0 -> 0x10
    assert raw[0] == 0x10
    # Byte 1: FrameType.VC_VOCAL_DOWN -> 0x03
    assert raw[1] == 0x03
    # Bytes 2..3: CodecId.PCM16_24K (2) in Big-Endian -> 0x00, 0x02
    assert raw[2:4] == b"\x00\x02"
    # Bytes 12..15: PayloadLen (4) in Big-Endian -> 0x00, 0x00, 0x00, 0x04
    assert raw[12:16] == b"\x00\x00\x00\x04"


def test_orm_models_avatar_fields() -> None:
    profile = CharacterProfile(id="char_001", name="TestChar", avatar_type="live2d")
    song = SongAsset(
        id="song_001",
        title="Demo",
        lead_vocal_path="songs/song_001/lead_vocal.wav",
        instrumental_path="songs/song_001/instrumental.opus",
    )
    job = FinetuneJob(id="job_001", character_id="char_001", target_engine="qwen3_tts")
    assert profile.avatar_type == "live2d"
    assert song.id == "song_001"
    assert job.target_engine == "qwen3_tts"
