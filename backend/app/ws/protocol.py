import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

HEADER_FORMAT = ">BBHIII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 16 bytes
PROTOCOL_VERSION = 1


class FrameFlags(IntFlag):
    NONE = 0
    EOS = 1 << 0
    KEYFRAME = 1 << 1


class FrameType(IntEnum):
    USER_AUDIO_UP = 0x01
    TTS_AUDIO_DOWN = 0x02
    VC_VOCAL_DOWN = 0x03
    AVATAR_VISEME_DOWN = 0x04


class CodecId(IntEnum):
    PCM16_16K = 1
    PCM16_24K = 2
    OPUS_24K = 3
    OPUS_48K = 4
    VISEME_MSGPACK = 5


@dataclass(slots=True)
class BinaryFrame:
    frame_type: FrameType
    codec: CodecId
    gen_id: int
    pts_ms: int
    payload: bytes
    flags: FrameFlags = FrameFlags.NONE
    version: int = PROTOCOL_VERSION

    def pack(self) -> bytes:
        ver_flags = ((self.version & 0x0F) << 4) | (int(self.flags) & 0x0F)
        header = struct.pack(
            HEADER_FORMAT,
            ver_flags,
            int(self.frame_type),
            int(self.codec),
            self.gen_id,
            self.pts_ms,
            len(self.payload),
        )
        return header + self.payload

    @classmethod
    def unpack(cls, raw: bytes) -> "BinaryFrame":
        if len(raw) < HEADER_SIZE:
            raise ValueError(f"Frame too short: {len(raw)} < {HEADER_SIZE}")

        ver_flags, ftype, codec, gen_id, pts_ms, payload_len = struct.unpack(
            HEADER_FORMAT, raw[:HEADER_SIZE]
        )
        expected_total = HEADER_SIZE + payload_len
        if len(raw) != expected_total:
            raise ValueError(
                f"Payload length mismatch: got {len(raw) - HEADER_SIZE}, expected {payload_len}"
            )

        version = (ver_flags >> 4) & 0x0F
        flags = FrameFlags(ver_flags & 0x0F)
        return cls(
            frame_type=FrameType(ftype),
            codec=CodecId(codec),
            gen_id=gen_id,
            pts_ms=pts_ms,
            payload=raw[HEADER_SIZE:],
            flags=flags,
            version=version,
        )
