import json
import os
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

TAG_PATTERN = re.compile(r"\[(emotion|action):([a-zA-Z0-9_-]+)\]")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？.!?\n])")

SINGING_TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "trigger_singing",
            "description": "当用户明确要求唱歌或翻唱某首歌曲时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "song_query": {"type": "string", "description": "歌曲名称或关键词"},
                    "key_shift": {"type": "integer", "description": "升降调半音数", "default": 0},
                },
                "required": ["song_query"],
            },
        },
    }
]


@dataclass(slots=True)
class LlmStreamEvent:
    kind: Literal["emotion", "text", "sing_intent", "done"]
    text: str = ""
    emotion: str | None = None
    action: str | None = None
    song_query: str | None = None
    key_shift: int = 0


def _is_tool_choice_error(exc: httpx.HTTPStatusError) -> bool:
    if exc.response is None or exc.response.status_code != 400:
        return False
    detail = str(exc).lower()
    return "tool" in detail and (
        "enable-auto-tool-choice" in detail
        or "tool-call-parser" in detail
        or "tool choice" in detail
    )


def strip_emotion_tags(raw_text: str) -> tuple[str, list[tuple[str, str]]]:
    """提取并剥离 [emotion:xxx][action:yyy] 标签，返回供 TTS 合成的纯文本。"""
    found: list[tuple[str, str]] = []

    def _repl(match: re.Match[str]) -> str:
        found.append((match.group(1), match.group(2)))
        return ""

    cleaned = TAG_PATTERN.sub(_repl, raw_text).strip()
    return cleaned, found


class LlmRouter:
    """对接远端 Ollama / vLLM (OpenAI 兼容流式接口)，支持情绪标签解析与唱歌意图路由。"""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        mock_mode: bool | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("HAKOCHUU_LLM_BASE_URL", "http://localhost:11434/v1")
        ).rstrip("/")
        self.model = model or os.getenv("HAKOCHUU_LLM_MODEL", "qwen2.5-social-3b-nb-chat")
        if mock_mode is None:
            llm_mock_env = os.getenv("HAKOCHUU_LLM_MOCK")
            if llm_mock_env is not None:
                self.mock_mode = llm_mock_env.lower() == "true"
            else:
                self.mock_mode = os.getenv("HAKOCHUU_MOCK_ENGINES", "true").lower() == "true"
        else:
            self.mock_mode = mock_mode

    async def stream_chat(
        self,
        user_text: str,
        system_prompt: str = "你是语音助手，请在回复开头附带 [emotion:neutral] 等情绪标签。",
        *,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> AsyncGenerator[LlmStreamEvent, None]:
        if self.mock_mode:
            async for ev in self._mock_stream(user_text):
                yield ev
            return

        target_base = (base_url or self.base_url).rstrip("/")
        payload: dict[str, object] = {
            "model": model or self.model,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "tools": SINGING_TOOL_SCHEMA,
            "tool_choice": "auto",
        }

        try:
            async for ev in self._stream_completions(target_base, payload):
                yield ev
        except httpx.HTTPStatusError as exc:
            if not _is_tool_choice_error(exc):
                raise
            logger.warning("[Debug] vLLM 未启用 tool parser，降级为无 tools 请求")
            fallback = {k: v for k, v in payload.items() if k not in {"tools", "tool_choice"}}
            async for ev in self._stream_completions(target_base, fallback):
                yield ev

    async def _stream_completions(
        self,
        target_base: str,
        payload: dict[str, object],
    ) -> AsyncGenerator[LlmStreamEvent, None]:
        buffer = ""
        tool_name = ""
        tool_args = ""

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST", f"{target_base}/chat/completions", json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise httpx.HTTPStatusError(
                        f"{resp.status_code} {resp.request.url}: {body}",
                        request=resp.request,
                        response=resp,
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})

                    if "tool_calls" in delta and delta["tool_calls"]:
                        tc = delta["tool_calls"][0].get("function", {})
                        tool_name = tc.get("name", tool_name)
                        tool_args += tc.get("arguments", "")
                        continue

                    content = delta.get("content")
                    if not content:
                        continue
                    buffer += content

                    # 按句号/感叹号/问号分句输出，降低 TTS 首包延迟
                    parts = SENTENCE_SPLIT_PATTERN.split(buffer)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            async for ev in self._emit_sentence(sentence):
                                yield ev
                        buffer = parts[-1]

        if buffer.strip():
            async for ev in self._emit_sentence(buffer):
                yield ev

        if tool_name == "trigger_singing":
            args = json.loads(tool_args) if tool_args else {}
            yield LlmStreamEvent(
                kind="sing_intent",
                song_query=args.get("song_query", ""),
                key_shift=int(args.get("key_shift", 0)),
            )

        yield LlmStreamEvent(kind="done")

    async def _emit_sentence(self, raw_sentence: str) -> AsyncGenerator[LlmStreamEvent, None]:
        cleaned, tags = strip_emotion_tags(raw_sentence)
        if tags:
            emotion = next((v for k, v in tags if k == "emotion"), None)
            action = next((v for k, v in tags if k == "action"), None)
            yield LlmStreamEvent(kind="emotion", emotion=emotion, action=action)
        if cleaned:
            yield LlmStreamEvent(kind="text", text=cleaned)

    async def _mock_stream(self, user_text: str) -> AsyncGenerator[LlmStreamEvent, None]:
        if any(kw in user_text for kw in ("唱歌", "翻唱", "唱一首")):
            yield LlmStreamEvent(kind="emotion", emotion="cheerful", action="nod")
            yield LlmStreamEvent(kind="text", text="好呀，我这就为你准备伴奏！")
            yield LlmStreamEvent(kind="sing_intent", song_query=user_text, key_shift=0)
            yield LlmStreamEvent(kind="done")
            return

        yield LlmStreamEvent(kind="emotion", emotion="cheerful", action="smile")
        yield LlmStreamEvent(kind="text", text=f"收到你的消息：{user_text}。")
        yield LlmStreamEvent(kind="done")
