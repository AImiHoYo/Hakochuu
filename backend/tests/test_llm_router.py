import json

import httpx
import pytest

from app.core.model_registry import get_default_llm_id, get_llm_config
from app.engines.llm_router import LlmRouter


@pytest.mark.asyncio
async def test_retry_without_tools_on_vllm_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payloads.append(body)
        if "tools" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            '"auto" tool choice requires --enable-auto-tool-choice '
                            "and --tool-call-parser to be set"
                        )
                    }
                },
            )
        lines = [
            'data: {"choices":[{"delta":{"content":"[emotion:cheerful]你好。"}}]}',
            "data: [DONE]",
            "",
        ]
        return httpx.Response(200, text="\n".join(lines))

    transport = httpx.MockTransport(handler)

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.engines.llm_router.httpx.AsyncClient", FakeClient)
    router = LlmRouter(
        base_url="http://llm.local/v1",
        model="qwen2.5-social-3b-nb-chat",
        mock_mode=False,
    )
    events = [ev async for ev in router.stream_chat("hi")]

    assert len(payloads) == 2
    assert "tools" in payloads[0]
    assert "tools" not in payloads[1]
    assert any(ev.kind == "text" and "你好" in ev.text for ev in events)
    assert any(ev.kind == "done" for ev in events)


def test_default_llm_id_matches_social_model() -> None:
    assert get_default_llm_id() == "qwen2.5-social-3b-nb-chat"
    cfg = get_llm_config("qwen2.5-social-3b-nb-chat")
    assert cfg is not None
    assert cfg["deployment"]["served_model_name"] == "qwen2.5-social-3b-nb-chat"
