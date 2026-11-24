from fastapi.testclient import TestClient

from app.core.model_registry import build_system_prompt, get_llm_config
from app.main import app


def test_list_llm_models_and_social_3b_profile() -> None:
    with TestClient(app) as client:
        resp = client.get("/v1/models/llms")
        assert resp.status_code == 200
        models = resp.json()
        social = next((m for m in models if m["id"] == "qwen2.5-social-3b-nb-chat"), None)
        assert social is not None
        assert social["character_binding"]["shares_global_characters"] is True

        scene_param = next(p for p in social["custom_params"] if p["key"] == "social_scene")
        scene_values = {opt["value"] for opt in scene_param["options"]}
        assert scene_values == {"elder", "girlfriend", "mentor", "stranger", "spouse"}


def test_build_system_prompt_merges_shared_character_and_scene() -> None:
    cfg = get_llm_config("qwen2.5-social-3b-nb-chat")
    assert cfg is not None
    prompt = build_system_prompt(
        cfg,
        llm_params={"social_scene": "girlfriend", "current_topic": "做饭创新"},
        character_name="星野",
        character_description="元气满满的工科女生",
    )
    assert "星野" in prompt
    assert "女友" in prompt
    assert "做饭创新" in prompt
