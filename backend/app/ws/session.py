import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.gpu_manager import get_gpu_manager
from app.core.logging import get_logger
from app.core.pipeline import RealtimePipeline
from app.core.spk_cache import SpeakerAssetEntry
from app.db.models import CharacterProfile
from app.db.session import AsyncSessionLocal
from app.engines.asr_engine import AsrEngine
from app.ws.protocol import BinaryFrame, CodecId, FrameFlags, FrameType

router = APIRouter(tags=["ws"])
logger = get_logger(__name__)


@dataclass
class SessionState:
    deploy_mode: str = "local"
    character_id: str = "char_001"
    llm_model_id: str | None = None
    llm_params: dict[str, object] = field(default_factory=dict)
    echo_loopback: bool = True
    emit_viseme: bool = False
    active_gen_id: int = 0
    codec: CodecId = CodecId.PCM16_24K
    audio_buffer: bytearray = field(default_factory=bytearray)
    active_task: asyncio.Task[None] | None = None


async def _sync_character_assets(pipeline: RealtimePipeline, character_id: str) -> None:
    """从 SQLite 读取角色音色与人设信息，写入 SpkEmbeddingCache。"""
    if not character_id or pipeline.spk_cache.get(character_id) is not None:
        return
    try:
        async with AsyncSessionLocal() as db:
            char = await db.get(CharacterProfile, character_id)
            if char is None:
                return
            pipeline.spk_cache.put(
                SpeakerAssetEntry(
                    character_id=char.id,
                    name=char.name,
                    description=char.description,
                    ref_speech_path=char.ref_speech_path,
                    ref_speech_text=char.ref_speech_text,
                    ref_sing_path=char.ref_sing_path,
                    lora_path=char.tts_lora_path,
                    embedding_ready=bool(char.speaker_embedding_path or char.ref_speech_path),
                )
            )
    except Exception:
        logger.exception("[Debug] 读取角色资产失败 (%s)", character_id)


async def _run_pipeline_turn(
    websocket: WebSocket,
    pipeline: RealtimePipeline,
    state: SessionState,
    user_text: str,
    gen_id: int,
) -> None:
    try:
        await _sync_character_assets(pipeline, state.character_id)
        async for out in pipeline.process_text_turn(
            user_text=user_text,
            character_id=state.character_id,
            gen_id=gen_id,
            is_cancelled=lambda gid: gid < state.active_gen_id,
            codec=state.codec,
            llm_model_id=state.llm_model_id,
            llm_params=state.llm_params,
            emit_viseme=state.emit_viseme,
        ):
            if gen_id < state.active_gen_id:
                return
            if out.signal is not None:
                await websocket.send_json(out.signal)
            if out.binary_frame is not None:
                await websocket.send_bytes(out.binary_frame.pack())
    except asyncio.CancelledError:
        logger.debug("流水线任务已取消 gen_id=%d", gen_id)
        return
    except Exception as exc:
        logger.exception("[Debug] Pipeline 异常 gen_id=%d", gen_id)
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "ERROR", "detail": str(exc)})


@router.websocket("/v1/session")
async def ws_session(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WebSocket /v1/session 已建立连接")
    state = SessionState()
    asr_engine = AsrEngine()
    pipeline = RealtimePipeline(gpu_manager=get_gpu_manager())

    async def cancel_active_turn() -> None:
        if state.active_task and not state.active_task.done():
            state.active_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.active_task
        state.active_task = None

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            if "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "ERROR", "detail": "Invalid JSON"})
                    continue

                msg_type = payload.get("type")
                if msg_type == "HELLO":
                    state.deploy_mode = payload.get("deploy_mode", "local")
                    state.character_id = payload.get("character_id", "char_001")
                    state.llm_model_id = payload.get("llm_model_id") or state.llm_model_id
                    if isinstance(payload.get("llm_params"), dict):
                        state.llm_params = payload["llm_params"]
                    if "echo_loopback" in payload:
                        state.echo_loopback = bool(payload["echo_loopback"])
                    caps = payload.get("capabilities")
                    if isinstance(caps, list) and "avatar_viseme_v1" in caps:
                        state.emit_viseme = True
                    state.codec = (
                        CodecId.OPUS_24K if state.deploy_mode == "lan" else CodecId.PCM16_24K
                    )
                    await _sync_character_assets(pipeline, state.character_id)
                    logger.info(
                        "HELLO 握手 mode=%s char=%s model=%s codec=%s echo=%s",
                        state.deploy_mode,
                        state.character_id,
                        state.llm_model_id or "default",
                        state.codec.name,
                        state.echo_loopback,
                    )
                    await websocket.send_json(
                        {
                            "type": "HELLO_ACK",
                            "protocol_version": "1.0",
                            "deploy_mode": state.deploy_mode,
                            "negotiated_codec": int(state.codec),
                            "active_gen_id": state.active_gen_id,
                        }
                    )
                elif msg_type == "INTERRUPT":
                    new_gen_id = int(payload.get("gen_id", state.active_gen_id + 1))
                    if new_gen_id > state.active_gen_id:
                        state.active_gen_id = new_gen_id
                    state.audio_buffer.clear()
                    await cancel_active_turn()
                    logger.info("收到 INTERRUPT 打断，active_gen_id -> %d", state.active_gen_id)
                    await websocket.send_json(
                        {
                            "type": "INTERRUPT_ACK",
                            "active_gen_id": state.active_gen_id,
                        }
                    )
                elif msg_type == "TEXT_TURN":
                    user_text = str(payload.get("text", "")).strip()
                    if not user_text:
                        continue
                    if payload.get("character_id"):
                        state.character_id = str(payload["character_id"])
                    if payload.get("llm_model_id"):
                        state.llm_model_id = str(payload["llm_model_id"])
                    if isinstance(payload.get("llm_params"), dict):
                        state.llm_params = payload["llm_params"]
                    turn_gen_id = int(payload.get("gen_id", state.active_gen_id or 1))
                    if turn_gen_id > state.active_gen_id:
                        state.active_gen_id = turn_gen_id
                    await cancel_active_turn()
                    logger.info("收到 TEXT_TURN gen_id=%d len=%d", turn_gen_id, len(user_text))
                    state.active_task = asyncio.create_task(
                        _run_pipeline_turn(websocket, pipeline, state, user_text, turn_gen_id)
                    )
                elif msg_type == "PING":
                    await websocket.send_json({"type": "PONG"})
                else:
                    await websocket.send_json(
                        {"type": "ERROR", "detail": f"Unsupported signal: {msg_type}"}
                    )

            elif "bytes" in message and message["bytes"] is not None:
                try:
                    frame = BinaryFrame.unpack(message["bytes"])
                except ValueError as exc:
                    await websocket.send_json({"type": "ERROR", "detail": str(exc)})
                    continue

                if frame.gen_id < state.active_gen_id:
                    continue

                if frame.frame_type == FrameType.USER_AUDIO_UP:
                    if frame.flags & FrameFlags.EOS:
                        state.audio_buffer.extend(frame.payload)
                        pcm_data = bytes(state.audio_buffer)
                        state.audio_buffer.clear()
                        asr_t0 = time.perf_counter()
                        try:
                            text = await asr_engine.transcribe_pcm16(pcm_data)
                        except (httpx.ConnectError, httpx.HTTPError) as exc:
                            logger.warning(
                                "[Debug] 缺少 ASR 模型 (%s): %s", asr_engine.server_url, exc
                            )
                            await websocket.send_json(
                                {
                                    "type": "DEBUG_WARNING",
                                    "gen_id": frame.gen_id,
                                    "component": "ASR",
                                    "message": "[Debug] 缺少 ASR 模型服务，无法识别语音输入",
                                }
                            )
                            continue
                        asr_ms = (time.perf_counter() - asr_t0) * 1000
                        logger.info(
                            "ASR 转写完成 gen_id=%d bytes=%d latency_ms=%.1f text=%r",
                            frame.gen_id,
                            len(pcm_data),
                            asr_ms,
                            text[:60],
                        )
                        await websocket.send_json(
                            {
                                "type": "ASR_RESULT",
                                "gen_id": frame.gen_id,
                                "text": text,
                            }
                        )
                        if text:
                            await cancel_active_turn()
                            state.active_task = asyncio.create_task(
                                _run_pipeline_turn(
                                    websocket, pipeline, state, text, frame.gen_id
                                )
                            )
                    else:
                        state.audio_buffer.extend(frame.payload)
                        if state.echo_loopback:
                            echo_frame = BinaryFrame(
                                frame_type=FrameType.TTS_AUDIO_DOWN,
                                codec=frame.codec,
                                gen_id=frame.gen_id,
                                pts_ms=frame.pts_ms,
                                payload=frame.payload,
                                flags=frame.flags,
                            )
                            await websocket.send_bytes(echo_frame.pack())
    except WebSocketDisconnect:
        logger.info("WebSocket /v1/session 客户端断开连接")
        await cancel_active_turn()
        return
    finally:
        await cancel_active_turn()
