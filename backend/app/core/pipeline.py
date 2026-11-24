import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.gpu_manager import GPUManager
from app.core.logging import get_logger
from app.core.model_registry import build_system_prompt, get_default_llm_id, get_llm_config
from app.core.spk_cache import SpkEmbeddingCache
from app.engines.lip_sync import extract_viseme_payload
from app.engines.llm_router import LlmRouter
from app.engines.tts_engine import TtsEngine
from app.engines.vc_engine import VcEngine
from app.ws.protocol import BinaryFrame, CodecId, FrameFlags, FrameType

logger = get_logger(__name__)


@dataclass(slots=True)
class PipelineOutput:
    signal: dict[str, object] | None = None
    binary_frame: BinaryFrame | None = None


class RealtimePipeline:
    """串联 ASR 文本 -> LlmRouter -> GPUManager -> TTS/VC -> 16-Byte 二进制帧下发。"""

    def __init__(
        self,
        gpu_manager: GPUManager | None = None,
        llm_router: LlmRouter | None = None,
        tts_engine: TtsEngine | None = None,
        vc_engine: VcEngine | None = None,
        spk_cache: SpkEmbeddingCache | None = None,
    ) -> None:
        self.gpu_manager = gpu_manager or GPUManager()
        self.llm_router = llm_router or LlmRouter()
        self.tts_engine = tts_engine or TtsEngine()
        self.vc_engine = vc_engine or VcEngine()
        self.spk_cache = spk_cache or SpkEmbeddingCache()

        self.gpu_manager.register_engines(
            tts_engine=self.tts_engine,
            vc_engine=self.vc_engine,
        )

    async def process_text_turn(
        self,
        user_text: str,
        character_id: str,
        gen_id: int,
        is_cancelled: Callable[[int], bool] | None = None,
        codec: CodecId = CodecId.PCM16_24K,
        llm_model_id: str | None = None,
        llm_params: dict[str, Any] | None = None,
        character_name: str | None = None,
        character_description: str | None = None,
        emit_viseme: bool = False,
    ) -> AsyncGenerator[PipelineOutput, None]:
        current_emotion = "neutral"
        pts_ms = 0
        tts_warned = False
        vc_warned = False

        cached_spk = self.spk_cache.get(character_id)
        resolved_char_name = character_name or (cached_spk.name if cached_spk else None)
        resolved_char_desc = character_description or (
            cached_spk.description if cached_spk else None
        )
        ref_speech_path = cached_spk.ref_speech_path if cached_spk else None
        ref_speech_text = cached_spk.ref_speech_text if cached_spk else None
        ref_sing_path = cached_spk.ref_sing_path if cached_spk else None
        lora_path = cached_spk.lora_path if cached_spk else None
        self.tts_engine.attach_lora(lora_path)

        resolved_model_id = llm_model_id or get_default_llm_id()
        model_cfg = get_llm_config(resolved_model_id) if resolved_model_id else None
        deploy_cfg = model_cfg.get("deployment", {}) if model_cfg else {}
        system_prompt = build_system_prompt(
            model_cfg,
            llm_params=llm_params,
            character_name=resolved_char_name,
            character_description=resolved_char_desc,
        )
        params = llm_params or {}
        temperature = float(params.get("temperature", 0.7))
        max_tokens = int(params.get("max_tokens", 256))

        turn_t0 = time.perf_counter()
        llm_first_logged = False
        logger.info(
            "流水线开始 gen_id=%d character=%s model=%s",
            gen_id,
            character_id,
            resolved_model_id or "default",
        )

        try:
            async for ev in self.llm_router.stream_chat(
                user_text,
                system_prompt=system_prompt,
                base_url=deploy_cfg.get("endpoint"),
                model=deploy_cfg.get("served_model_name"),
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                if is_cancelled and is_cancelled(gen_id):
                    logger.info("流水线已打断 gen_id=%d", gen_id)
                    return

                if ev.kind == "emotion":
                    current_emotion = ev.emotion or "neutral"
                    yield PipelineOutput(
                        signal={
                            "type": "EMOTION_TAG",
                            "gen_id": gen_id,
                            "emotion": current_emotion,
                            "action": ev.action,
                        }
                    )
                elif ev.kind == "text" and ev.text:
                    if not llm_first_logged:
                        llm_first_logged = True
                        logger.info(
                            "LLM 首句延迟 gen_id=%d latency_ms=%.1f",
                            gen_id,
                            (time.perf_counter() - turn_t0) * 1000,
                        )
                    # 无论语音模型是否在线，先下发文本回复给前端
                    yield PipelineOutput(
                        signal={"type": "LLM_TEXT", "gen_id": gen_id, "text": ev.text}
                    )
                    try:
                        await self.gpu_manager.ensure_mode("chat")
                        tts_kwargs: dict[str, Any] = {}
                        if ref_speech_path is not None:
                            tts_kwargs["ref_speech_path"] = ref_speech_path
                        if ref_speech_text is not None:
                            tts_kwargs["ref_speech_text"] = ref_speech_text
                        tts_t0 = time.perf_counter()
                        tts_first_logged = False
                        tts_frames = 0
                        async for chunk in self.tts_engine.synthesize_stream(
                            text=ev.text,
                            character_id=character_id,
                            emotion=current_emotion,
                            **tts_kwargs,
                        ):
                            if is_cancelled and is_cancelled(gen_id):
                                return
                            if not tts_first_logged:
                                tts_first_logged = True
                                logger.info(
                                    "TTS 首包延迟 gen_id=%d latency_ms=%.1f",
                                    gen_id,
                                    (time.perf_counter() - tts_t0) * 1000,
                                )
                            tts_frames += 1
                            yield PipelineOutput(
                                binary_frame=BinaryFrame(
                                    frame_type=FrameType.TTS_AUDIO_DOWN,
                                    codec=codec,
                                    gen_id=gen_id,
                                    pts_ms=pts_ms,
                                    payload=chunk,
                                    flags=FrameFlags.NONE,
                                )
                            )
                            if emit_viseme:
                                yield PipelineOutput(
                                    binary_frame=BinaryFrame(
                                        frame_type=FrameType.AVATAR_VISEME_DOWN,
                                        codec=CodecId.VISEME_MSGPACK,
                                        gen_id=gen_id,
                                        pts_ms=pts_ms,
                                        payload=extract_viseme_payload(chunk),
                                        flags=FrameFlags.NONE,
                                    )
                                )
                            pts_ms += 20
                        logger.debug(
                            "TTS 分句完成 gen_id=%d frames=%d elapsed_ms=%.1f",
                            gen_id,
                            tts_frames,
                            (time.perf_counter() - tts_t0) * 1000,
                        )
                    except (httpx.ConnectError, httpx.HTTPError) as exc:
                        logger.warning("[Debug] 缺少 TTS 模型 (%s): %s", self.tts_engine.server_url, exc)
                        if not tts_warned:
                            tts_warned = True
                            yield PipelineOutput(
                                signal={
                                    "type": "DEBUG_WARNING",
                                    "gen_id": gen_id,
                                    "component": "TTS",
                                    "message": "[Debug] 缺少 TTS 模型（已降级为仅返回文本回复）",
                                }
                            )
                elif ev.kind == "sing_intent":
                    song_id = ev.song_query or "song_demo"
                    yield PipelineOutput(
                        signal={
                            "type": "SING_START",
                            "gen_id": gen_id,
                            "song_id": song_id,
                            "key_shift": ev.key_shift,
                        }
                    )
                    try:
                        await self.gpu_manager.ensure_mode("sing")
                        vc_kwargs: dict[str, Any] = {}
                        if ref_sing_path is not None:
                            vc_kwargs["ref_sing_path"] = ref_sing_path
                        vc_t0 = time.perf_counter()
                        vc_first_logged = False
                        vc_frames = 0
                        async for vocal_pts, vocal_chunk in self.vc_engine.convert_song_stream(
                            song_id=song_id,
                            character_id=character_id,
                            key_shift=ev.key_shift,
                            **vc_kwargs,
                        ):
                            if is_cancelled and is_cancelled(gen_id):
                                return
                            if not vc_first_logged:
                                vc_first_logged = True
                                logger.info(
                                    "VC 首包延迟 gen_id=%d song_id=%s latency_ms=%.1f",
                                    gen_id,
                                    song_id,
                                    (time.perf_counter() - vc_t0) * 1000,
                                )
                            vc_frames += 1
                            yield PipelineOutput(
                                binary_frame=BinaryFrame(
                                    frame_type=FrameType.VC_VOCAL_DOWN,
                                    codec=codec,
                                    gen_id=gen_id,
                                    pts_ms=vocal_pts,
                                    payload=vocal_chunk,
                                    flags=FrameFlags.NONE,
                                )
                            )
                            if emit_viseme:
                                yield PipelineOutput(
                                    binary_frame=BinaryFrame(
                                        frame_type=FrameType.AVATAR_VISEME_DOWN,
                                        codec=CodecId.VISEME_MSGPACK,
                                        gen_id=gen_id,
                                        pts_ms=vocal_pts,
                                        payload=extract_viseme_payload(vocal_chunk),
                                        flags=FrameFlags.NONE,
                                    )
                                )
                        logger.info(
                            "VC 转换完成 gen_id=%d song_id=%s frames=%d elapsed_ms=%.1f",
                            gen_id,
                            song_id,
                            vc_frames,
                            (time.perf_counter() - vc_t0) * 1000,
                        )
                    except (httpx.ConnectError, httpx.HTTPError) as exc:
                        logger.warning("[Debug] 缺少 VC 模型 (%s): %s", self.vc_engine.server_url, exc)
                        if not vc_warned:
                            vc_warned = True
                            yield PipelineOutput(
                                signal={
                                    "type": "DEBUG_WARNING",
                                    "gen_id": gen_id,
                                    "component": "VC",
                                    "message": "[Debug] 缺少 VC 模型（无法生成翻唱人声干声）",
                                }
                            )
                elif ev.kind == "done":
                    logger.info(
                        "流水线回合结束 gen_id=%d total_ms=%.1f",
                        gen_id,
                        (time.perf_counter() - turn_t0) * 1000,
                    )
                    yield PipelineOutput(signal={"type": "TURN_DONE", "gen_id": gen_id})
        except httpx.ConnectError as exc:
            llm_ep = deploy_cfg.get("endpoint", self.llm_router.base_url)
            logger.warning("[Debug] 缺少 LLM 服务 (%s): %s", llm_ep, exc)
            yield PipelineOutput(
                signal={
                    "type": "DEBUG_WARNING",
                    "gen_id": gen_id,
                    "component": "LLM",
                    "message": "[Debug] 缺少 LLM 模型服务，请确认 vLLM/Ollama 已启动",
                }
            )
            yield PipelineOutput(signal={"type": "TURN_DONE", "gen_id": gen_id})
        except httpx.HTTPStatusError as exc:
            llm_ep = deploy_cfg.get("endpoint", self.llm_router.base_url)
            logger.warning("[Debug] LLM 请求失败 (%s): %s", llm_ep, exc)
            yield PipelineOutput(
                signal={
                    "type": "DEBUG_WARNING",
                    "gen_id": gen_id,
                    "component": "LLM",
                    "message": (
                        f"[Debug] LLM 请求失败 "
                        f"({exc.response.status_code if exc.response else '?'})"
                    ),
                }
            )
            yield PipelineOutput(signal={"type": "TURN_DONE", "gen_id": gen_id})
        except httpx.HTTPError as exc:
            llm_ep = deploy_cfg.get("endpoint", self.llm_router.base_url)
            logger.warning("[Debug] LLM 请求异常 (%s): %s", llm_ep, exc)
            yield PipelineOutput(
                signal={
                    "type": "DEBUG_WARNING",
                    "gen_id": gen_id,
                    "component": "LLM",
                    "message": f"[Debug] LLM 请求异常: {exc}",
                }
            )
            yield PipelineOutput(signal={"type": "TURN_DONE", "gen_id": gen_id})
