"""0x04 口型同步 (AVATAR_VISEME_DOWN) 包络与 Viseme 权重提取器。

从 20ms PCM16 音频帧提取归一化 RMS 能量与基础元音口型权重（aa/ih/ou/ee/oh/rms），
编码为紧凑 JSON 字节供前端 LipSyncController 按 PTS 对齐渲染。
"""

from __future__ import annotations

import json
import math
import struct


def extract_viseme_weights(pcm16_bytes: bytes) -> dict[str, float]:
    """从 16-bit 小端 PCM 字节提取归一化 RMS 与启发式 Viseme 权重。"""
    count = len(pcm16_bytes) // 2
    if count <= 0:
        return {"aa": 0.0, "ih": 0.0, "ou": 0.0, "ee": 0.0, "oh": 0.0, "rms": 0.0}

    samples = struct.unpack(f"<{count}h", pcm16_bytes[: count * 2])
    sum_sq = 0.0
    zero_crossings = 0
    prev = samples[0]
    for s in samples:
        norm = s / 32768.0
        sum_sq += norm * norm
        if (s >= 0) != (prev >= 0):
            zero_crossings += 1
        prev = s

    rms = math.sqrt(sum_sq / count)
    # 语音响度映射至 [0, 1]（通常人声 RMS 在 0.02~0.25 之间）
    level = min(1.0, max(0.0, rms * 4.0))
    zcr = zero_crossings / count

    if level < 0.03:
        return {"aa": 0.0, "ih": 0.0, "ou": 0.0, "ee": 0.0, "oh": 0.0, "rms": round(level, 3)}

    # 按过零率粗分高低频元音口型权重
    if zcr > 0.25:
        aa, ih, ou, ee, oh = level * 0.25, level * 0.8, level * 0.1, level * 0.7, level * 0.15
    elif zcr > 0.12:
        aa, ih, ou, ee, oh = level * 0.85, level * 0.35, level * 0.25, level * 0.3, level * 0.45
    else:
        aa, ih, ou, ee, oh = level * 0.5, level * 0.15, level * 0.75, level * 0.15, level * 0.8

    return {
        "aa": round(aa, 3),
        "ih": round(ih, 3),
        "ou": round(ou, 3),
        "ee": round(ee, 3),
        "oh": round(oh, 3),
        "rms": round(level, 3),
    }


def extract_viseme_payload(pcm16_bytes: bytes) -> bytes:
    weights = extract_viseme_weights(pcm16_bytes)
    return json.dumps(weights, separators=(",", ":")).encode("utf-8")
