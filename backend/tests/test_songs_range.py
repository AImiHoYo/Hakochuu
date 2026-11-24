import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import songs as songs_api
from app.engines.separator import SeparationResult
from app.main import app


def test_instrumental_range_206_and_etag_304(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAKOCHUU_STORAGE_DIR", str(tmp_path))

    song_dir = tmp_path / "songs" / "song_demo"
    song_dir.mkdir(parents=True, exist_ok=True)
    inst_file = song_dir / "instrumental.opus"
    payload = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    inst_file.write_bytes(payload)

    with TestClient(app) as client:
        # 1. 全量 200 下载与 ETag 检查
        full = client.get("/v1/songs/song_demo/instrumental")
        assert full.status_code == 200
        assert full.content == payload
        assert full.headers["accept-ranges"] == "bytes"
        etag = full.headers["etag"]

        # 2. If-None-Match 命中 304
        not_mod = client.get(
            "/v1/songs/song_demo/instrumental",
            headers={"If-None-Match": etag},
        )
        assert not_mod.status_code == 304

        # 3. Range 206 分块下载
        partial = client.get(
            "/v1/songs/song_demo/instrumental",
            headers={"Range": "bytes=4-13"},
        )
        assert partial.status_code == 206
        assert partial.content == b"456789ABCD"
        assert partial.headers["content-range"] == f"bytes 4-13/{len(payload)}"

        # 4. 越界 Range 返回 416
        invalid = client.get(
            "/v1/songs/song_demo/instrumental",
            headers={"Range": "bytes=999-1024"},
        )
        assert invalid.status_code == 416


def test_parse_song_filename() -> None:
    artist, title = songs_api.parse_song_filename(
        "MARUMOCHI from HoneyWorks,ハコニワリリィ - 質問、恋って何でしょうか.flac"
    )
    assert artist == "MARUMOCHI from HoneyWorks,ハコニワリリィ"
    assert title == "質問、恋って何でしょうか"

    artist2, title2 = songs_api.parse_song_filename("01. HoneyWorks,江口拓也 - 女の子の愛って何？.flac")
    assert artist2 == "HoneyWorks,江口拓也"
    assert title2 == "女の子の愛って何？"

    artist3, title3 = songs_api.parse_song_filename("晴天.wav")
    assert artist3 is None
    assert title3 == "晴天"


def test_async_song_separation_job_and_sequential_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAKOCHUU_STORAGE_DIR", str(tmp_path))
    songs_api._SEPARATION_JOBS.clear()

    def _fake_separate(input_audio: Path, song_id: str, output_root: Path, on_progress=None):
        if on_progress:
            on_progress(50, "模拟分离中")
        out_dir = output_root / "songs" / song_id
        out_dir.mkdir(parents=True, exist_ok=True)
        vocal = out_dir / "lead_vocal.wav"
        inst = out_dir / "instrumental.opus"
        vocal.write_bytes(b"RIFFdemo")
        inst.write_bytes(b"OggSdemo")
        return SeparationResult(
            song_id=song_id,
            lead_vocal_path=vocal,
            instrumental_path=inst,
            duration_ms=123000,
        )

    monkeypatch.setattr(songs_api, "separate_song_sync", _fake_separate)

    with TestClient(app) as client:
        next_before = int(client.get("/v1/songs/next-id").json()["next_song_id"])

        resp = client.post(
            "/v1/songs/separate/jobs",
            files={
                "file": (
                    "HoneyWorks,江口拓也 - 女の子の愛って何？.flac",
                    b"fake-flac-bytes",
                    "audio/flac",
                )
            },
        )
        assert resp.status_code == 202
        job = resp.json()
        assert job["song_id"] == str(next_before)
        assert job["artist"] == "HoneyWorks,江口拓也"
        assert job["title"] == "女の子の愛って何？"

        # SSE 流应推进至 completed
        events = []
        with client.stream("GET", f"/v1/songs/separate/jobs/{job['id']}/stream") as stream_resp:
            assert stream_resp.status_code == 200
            for line in stream_resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        assert events[-1]["status"] == "completed"
        assert events[-1]["progress"] == 100

        # 下一首 song_id 应自动递增 +1
        next_after = int(client.get("/v1/songs/next-id").json()["next_song_id"])
        assert next_after == next_before + 1

        # 曲库列表中应包含刚分离的新歌
        songs = client.get("/v1/songs").json()
        matched = [s for s in songs if s["id"] == str(next_before)]
        assert len(matched) == 1
        assert matched[0]["artist"] == "HoneyWorks,江口拓也"
        assert matched[0]["title"] == "女の子の愛って何？"
