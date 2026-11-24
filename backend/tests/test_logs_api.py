import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAKOCHUU_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("HAKOCHUU_LLM_BASE_URL", "http://localhost:19991/v1")
    monkeypatch.setenv("HAKOCHUU_ASR_SERVER_URL", "http://localhost:19992")
    monkeypatch.setenv("HAKOCHUU_TTS_SERVER_URL", "http://localhost:19993")
    monkeypatch.setenv("HAKOCHUU_VC_SERVER_URL", "http://localhost:19994")
    return tmp_path


def test_list_log_services(log_dir: Path) -> None:
    (log_dir / "uvicorn.log").write_text(
        "2026-11-24 10:00:00.000 | INFO    | gw | ok\n", encoding="utf-8"
    )
    (log_dir / "asr.log").write_text("line1\n", encoding="utf-8")

    client = TestClient(app)
    resp = client.get("/v1/logs/services")
    assert resp.status_code == 200
    services = {item["service"]: item for item in resp.json()["services"]}

    assert set(services.keys()) == {"gateway", "vllm", "asr", "tts", "vc"}
    assert services["gateway"]["available"] is True
    assert services["gateway"]["healthy"] is True
    assert services["gateway"]["size_bytes"] > 0
    assert services["asr"]["available"] is True
    assert services["tts"]["available"] is False


def test_get_logs_tail_level_and_keyword_filter(log_dir: Path) -> None:
    log_lines = [
        "2026-11-24 10:00:01.000 | INFO    | hakochuu.pipeline | 流水线开始 gen_id=1",
        "2026-11-24 10:00:02.000 | WARNING | hakochuu.pipeline | [Debug] 缺少 TTS 模型",
        "2026-11-24 10:00:03.000 | ERROR   | hakochuu.session | Pipeline 异常 gen_id=2",
        "2026-11-24 10:00:04.000 | INFO    | hakochuu.pipeline | LLM 首句延迟 gen_id=3 latency_ms=42.1",
        "2026-11-24 10:00:05.000 | DEBUG   | hakochuu.pipeline | TTS 分句完成 gen_id=3",
    ]
    (log_dir / "uvicorn.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    client = TestClient(app)

    r_tail = client.get("/v1/logs/uvicorn?lines=2")
    assert r_tail.status_code == 200
    data_tail = r_tail.json()
    assert data_tail["service"] == "gateway"
    assert len(data_tail["lines"]) == 2
    assert "gen_id=3" in data_tail["lines"][0]

    r_warn = client.get("/v1/logs/gateway?level=WARNING")
    assert r_warn.status_code == 200
    assert len(r_warn.json()["lines"]) == 1
    assert "缺少 TTS 模型" in r_warn.json()["lines"][0]

    r_kw = client.get("/v1/logs/gateway?level=INFO&q=latency_ms")
    assert r_kw.status_code == 200
    assert len(r_kw.json()["lines"]) == 1
    assert "42.1" in r_kw.json()["lines"][0]

    r_bad_level = client.get("/v1/logs/gateway?level=TRACE")
    assert r_bad_level.status_code == 400


def test_reject_unknown_service(log_dir: Path) -> None:
    client = TestClient(app)
    for bad in ("unknown", "redis", "arbitrary_file", "other_service"):
        r1 = client.get(f"/v1/logs/{bad}")
        assert r1.status_code == 404
        r2 = client.get(f"/v1/logs/{bad}/stream")
        assert r2.status_code == 404


def test_stream_logs_initial_tail(log_dir: Path) -> None:
    (log_dir / "tts.log").write_text(
        "2026-11-24 10:00:01.000 | INFO | tts | TTS 首包生成\n"
        "2026-11-24 10:00:02.000 | ERROR | tts | TTS 合成失败\n",
        encoding="utf-8",
    )

    from app.api.v1.logs import _line_matches, _read_tail_lines

    lines = _read_tail_lines(log_dir / "tts.log", limit=10, level="ERROR", query=None)
    assert len(lines) == 1
    assert "TTS 合成失败" in lines[0]
    assert _line_matches("2026-11-24 | WARNING | x | [Debug] DEBUG_WARNING", "WARNING", "debug")

    client = TestClient(app)
    with client.stream("GET", "/v1/logs/tts/stream?lines=10&level=ERROR") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        first_line = next(resp.iter_lines())
        assert first_line.startswith("data: ")
        payload = json.loads(first_line[6:])
        assert payload["service"] == "tts"
        assert "TTS 合成失败" in payload["line"]
