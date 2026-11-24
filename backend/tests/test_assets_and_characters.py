import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_upload_check_upload_and_register_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAKOCHUU_STORAGE_DIR", str(tmp_path))

    speech_bytes = b"RIFFdemo-speech-wav-data"
    sing_bytes = b"RIFFdemo-sing-wav-data"
    speech_sha = hashlib.sha256(speech_bytes).hexdigest()
    sing_sha = hashlib.sha256(sing_bytes).hexdigest()

    with TestClient(app) as client:
        # 1. 初次校验应均为 missing
        check1 = client.post(
            "/v1/assets/upload-check",
            json={"hashes": [speech_sha, sing_sha], "kind": "speech"},
        )
        assert check1.status_code == 200
        assert set(check1.json()["missing"]) == {speech_sha, sing_sha}

        # 2. 上传两段引导音频
        up_speech = client.post(
            "/v1/assets/upload",
            data={"sha256": speech_sha, "kind": "speech"},
            files={"file": ("ref_speech.wav", speech_bytes, "audio/wav")},
        )
        assert up_speech.status_code == 200
        speech_rel = up_speech.json()["path"]

        up_sing = client.post(
            "/v1/assets/upload",
            data={"sha256": sing_sha, "kind": "sing"},
            files={"file": ("ref_sing.wav", sing_bytes, "audio/wav")},
        )
        assert up_sing.status_code == 200
        sing_rel = up_sing.json()["path"]

        # 3. 再次校验应命中秒传缓存
        check2 = client.post(
            "/v1/assets/upload-check",
            json={"hashes": [speech_sha], "kind": "speech"},
        )
        assert check2.status_code == 200
        assert check2.json()["existing"] == [speech_sha]

        # 4. 注册角色档案
        reg = client.post(
            "/v1/characters/register",
            json={
                "name": "HakoTest",
                "description": "引导建档测试角色",
                "ref_speech_path": speech_rel,
                "ref_speech_text": "今天天气真不错，我们一起去散步吧。",
                "ref_sing_path": sing_rel,
                "avatar_type": "live2d",
            },
        )
        assert reg.status_code == 201
        char_data = reg.json()
        assert char_data["name"] == "HakoTest"
        assert char_data["avatar_type"] == "live2d"
        assert char_data["ref_speech_path"] is not None
        assert (tmp_path / char_data["ref_speech_path"]).is_file()

        # 5. 查询角色列表包含新角色
        listed = client.get("/v1/characters")
        assert listed.status_code == 200
        assert any(item["id"] == char_data["id"] for item in listed.json())

        # 6. 删除角色档案
        deleted = client.delete(f"/v1/characters/{char_data['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deleted"
        after = client.get(f"/v1/characters/{char_data['id']}")
        assert after.status_code == 404
