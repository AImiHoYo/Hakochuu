"""服务端日志查询与 SSE 实时流接口（严格使用服务白名单映射，严禁任意路径透传）。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/logs", tags=["logs"])

REPO_ROOT = Path(__file__).resolve().parents[4]
VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}


@dataclass(frozen=True, slots=True)
class LogServiceSpec:
    service: str
    label: str
    log_filename: str
    default_port: int
    health_path: str | None = "/health"
    env_url_key: str | None = None


_SERVICE_SPECS: dict[str, LogServiceSpec] = {
    "gateway": LogServiceSpec("gateway", "FastAPI 网关", "uvicorn.log", 23333, None, None),
    "vllm": LogServiceSpec("vllm", "vLLM 推理", "vllm.log", 11434, "/models", "HAKOCHUU_LLM_BASE_URL"),
    "asr": LogServiceSpec("asr", "ASR 识别", "asr.log", 23336, "/health", "HAKOCHUU_ASR_SERVER_URL"),
    "tts": LogServiceSpec("tts", "TTS 合成", "tts.log", 23334, "/health", "HAKOCHUU_TTS_SERVER_URL"),
    "vc": LogServiceSpec("vc", "Seed-VC 翻唱", "vc.log", 23335, "/health", "HAKOCHUU_VC_SERVER_URL"),
}

_SERVICE_ALIASES: dict[str, str] = {
    "gateway": "gateway",
    "uvicorn": "gateway",
    "vllm": "vllm",
    "asr": "asr",
    "tts": "tts",
    "vc": "vc",
}


def _storage_dir() -> Path:
    raw = os.getenv("HAKOCHUU_STORAGE_DIR", "").strip()
    return Path(raw).resolve() if raw else (REPO_ROOT / "storage").resolve()


def _resolve_service(service: str) -> LogServiceSpec:
    canonical = _SERVICE_ALIASES.get(service.strip().lower())
    if canonical is None:
        raise HTTPException(status_code=404, detail=f"未知日志服务: {service}")
    return _SERVICE_SPECS[canonical]


def _log_file_path(spec: LogServiceSpec) -> Path:
    return (_storage_dir() / spec.log_filename).resolve()


def _normalize_level(level: str | None) -> str | None:
    if level is None:
        return None
    cleaned = level.strip().upper()
    if not cleaned:
        return None
    if cleaned not in VALID_LEVELS:
        raise HTTPException(status_code=400, detail=f"不支持的日志级别: {level}")
    return "WARNING" if cleaned == "WARN" else cleaned


def _line_matches(line: str, level: str | None, query: str | None) -> bool:
    if level:
        upper = line.upper()
        if level == "WARNING":
            if not (re.search(r"\bWARN(?:ING)?\b", upper) or "DEBUG_WARNING" in upper):
                return False
        elif not re.search(rf"\b{re.escape(level)}\b", upper):
            return False
    if query and query.lower() not in line.lower():
        return False
    return True


def _file_meta(path: Path) -> tuple[bool, int, str | None]:
    if not path.is_file():
        return False, 0, None
    stat = path.stat()
    updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return True, stat.st_size, updated_at


def _read_tail_lines(
    path: Path,
    limit: int,
    level: str | None = None,
    query: str | None = None,
) -> list[str]:
    if not path.is_file() or limit <= 0:
        return []
    scan_limit = limit if (not level and not query) else max(limit * 10, 5000)
    buf: deque[str] = deque(maxlen=scan_limit)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            buf.append(raw_line.rstrip("\r\n"))
    if not level and not query:
        return list(buf)[-limit:]
    return [line for line in buf if _line_matches(line, level, query)][-limit:]


async def _check_port_health(spec: LogServiceSpec) -> bool:
    if spec.service == "gateway":
        return True
    base = os.getenv(spec.env_url_key, "").strip().rstrip("/") if spec.env_url_key else ""
    if not base:
        base = f"http://localhost:{spec.default_port}"
    url = f"{base}{spec.health_path or '/health'}"
    try:
        async with httpx.AsyncClient(timeout=0.8) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except httpx.HTTPError:
        return False


@router.get("/services")
async def list_log_services() -> dict[str, list[dict[str, object]]]:
    specs = list(_SERVICE_SPECS.values())
    health_results = await asyncio.gather(
        *[_check_port_health(spec) for spec in specs],
        return_exceptions=True,
    )
    items: list[dict[str, object]] = []
    for spec, healthy in zip(specs, health_results, strict=True):
        log_path = _log_file_path(spec)
        available, size_bytes, updated_at = _file_meta(log_path)
        port = (
            int(os.getenv("HAKOCHUU_PORT", str(spec.default_port)))
            if spec.service == "gateway"
            else spec.default_port
        )
        items.append(
            {
                "service": spec.service,
                "label": spec.label,
                "port": port,
                "log_file": spec.log_filename,
                "available": available,
                "size_bytes": size_bytes,
                "updated_at": updated_at,
                "healthy": healthy is True,
            }
        )
    return {"services": items}


@router.get("/{service}")
async def get_service_logs(
    service: str,
    lines: int = Query(200, ge=1, le=5000),
    level: str | None = Query(None),
    q: str | None = Query(None),
) -> dict[str, object]:
    spec = _resolve_service(service)
    norm_level = _normalize_level(level)
    query = q.strip() if q and q.strip() else None

    log_path = _log_file_path(spec)
    available, size_bytes, updated_at = _file_meta(log_path)
    result_lines = _read_tail_lines(log_path, limit=lines, level=norm_level, query=query)
    return {
        "service": spec.service,
        "label": spec.label,
        "port": spec.default_port,
        "available": available,
        "size_bytes": size_bytes,
        "updated_at": updated_at,
        "lines": result_lines,
        "returned": len(result_lines),
    }


@router.get("/{service}/stream")
async def stream_service_logs(
    request: Request,
    service: str,
    lines: int = Query(100, ge=0, le=1000),
    level: str | None = Query(None),
    q: str | None = Query(None),
) -> StreamingResponse:
    spec = _resolve_service(service)
    norm_level = _normalize_level(level)
    query = q.strip() if q and q.strip() else None
    log_path = _log_file_path(spec)

    async def event_generator():  # type: ignore[no-untyped-def]
        initial = _read_tail_lines(log_path, limit=lines, level=norm_level, query=query)
        for line in initial:
            payload = json.dumps({"service": spec.service, "line": line}, ensure_ascii=False)
            yield f"data: {payload}\n\n"

        offset = log_path.stat().st_size if log_path.is_file() else 0
        partial = ""
        last_heartbeat = time.monotonic()

        try:
            while True:
                if await request.is_disconnected():
                    break

                if log_path.is_file():
                    size = log_path.stat().st_size
                    if size < offset:
                        offset = 0
                        partial = ""
                    if size > offset:
                        with log_path.open("r", encoding="utf-8", errors="replace") as f:
                            f.seek(offset)
                            chunk = f.read()
                            offset = f.tell()
                        if chunk:
                            text = partial + chunk
                            parts = text.splitlines(keepends=True)
                            partial = ""
                            for part in parts:
                                if not part.endswith(("\n", "\r")):
                                    partial = part
                                    continue
                                clean_line = part.rstrip("\r\n")
                                if _line_matches(clean_line, norm_level, query):
                                    payload = json.dumps(
                                        {"service": spec.service, "line": clean_line},
                                        ensure_ascii=False,
                                    )
                                    yield f"data: {payload}\n\n"
                                    last_heartbeat = time.monotonic()

                now = time.monotonic()
                if now - last_heartbeat >= 15.0:
                    yield ": keep-alive\n\n"
                    last_heartbeat = now

                await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
