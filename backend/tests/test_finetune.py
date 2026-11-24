from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_create_and_stream_finetune_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAKOCHUU_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("HAKOCHUU_MOCK_ENGINES", "true")

    with TestClient(app) as client:
        reg = client.post(
            "/v1/characters/register",
            json={"name": "FinetuneChar", "avatar_type": "vrm"},
        )
        assert reg.status_code == 201
        char_id = reg.json()["id"]

        created = client.post(
            "/v1/finetune/jobs",
            json={"character_id": char_id, "target_engine": "qwen3_tts"},
        )
        assert created.status_code == 201
        job_id = created.json()["id"]
        assert created.json()["status"] == "queued"

        listed = client.get("/v1/finetune/jobs", params={"character_id": char_id})
        assert listed.status_code == 200
        assert any(j["id"] == job_id for j in listed.json())

        stream = client.get(f"/v1/finetune/jobs/{job_id}/stream")
        assert stream.status_code == 200
        assert "completed" in stream.text
