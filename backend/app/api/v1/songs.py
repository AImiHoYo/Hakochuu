import asyncio
import hashlib
import json
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assets import get_storage_root
from app.core.logging import get_logger
from app.db.models import SongAsset
from app.db.session import AsyncSessionLocal, get_db
from app.engines.separator import separate_song_sync

router = APIRouter(prefix="/songs", tags=["songs"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
logger = get_logger(__name__)

CHUNK_SIZE = 64 * 1024
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
TRACK_PREFIX_RE = re.compile(r"^\d{1,3}[\.\s、_-]+\s*")


class SongOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    artist: str | None
    duration_ms: int


class SongSeparationJobOut(BaseModel):
    id: str
    song_id: str
    title: str
    artist: str | None
    status: Literal["queued", "running", "completed", "failed"]
    progress: int
    stage: str
    duration_ms: int | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class _SeparationJobRecord:
    id: str
    song_id: str
    title: str
    artist: str | None
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    progress: int = 0
    stage: str = "等待排队中"
    duration_ms: int | None = None
    error_message: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_SEPARATION_JOBS: dict[str, _SeparationJobRecord] = {}
_SEPARATION_TASKS: set[asyncio.Task[None]] = set()
_SEPARATION_SEMAPHORE = asyncio.Semaphore(1)


def parse_song_filename(filename: str | None) -> tuple[str | None, str]:
    """从文件名自动提取 (artist, title)，兼容 `歌手 - 歌名.flac` 格式。"""
    if not filename:
        return None, "未命名歌曲"
    stem = Path(filename).stem.strip()
    if not stem:
        return None, "未命名歌曲"

    for sep in (" - ", " – ", " — ", " － "):
        if sep in stem:
            left, right = stem.split(sep, 1)
            artist = TRACK_PREFIX_RE.sub("", left.strip()).strip()
            title = right.strip()
            if title:
                return (artist or None), title

    cleaned = TRACK_PREFIX_RE.sub("", stem).strip()
    return None, cleaned or stem


async def _compute_next_song_id(db: AsyncSession) -> str:
    result = await db.execute(select(SongAsset.id))
    existing_ids = list(result.scalars().all())
    for job in _SEPARATION_JOBS.values():
        if job.status != "failed":
            existing_ids.append(job.song_id)
    max_num = 0
    for sid in existing_ids:
        if sid.isdigit():
            val = int(sid)
            if val > max_num:
                max_num = val
    return str(max_num + 1)


def _song_sort_key(song: SongAsset) -> tuple[int, int, str]:
    if song.id.isdigit():
        return (0, int(song.id), song.id)
    return (1, 0, song.id)


def _guess_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".opus" or ext == ".ogg":
        return "audio/ogg"
    if ext == ".wav":
        return "audio/wav"
    if ext == ".mp3":
        return "audio/mpeg"
    return "application/octet-stream"


def _compute_etag(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    return f'"{hashlib.sha256(raw).hexdigest()[:24]}"'


def _iter_file_range(path: Path, start: int, end: int):
    with path.open("rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


async def _upsert_song_asset(
    db: AsyncSession,
    *,
    song_id: str,
    title: str,
    artist: str | None,
    duration_ms: int,
    lead_vocal_path: Path,
    instrumental_path: Path,
) -> SongAsset:
    existing = await db.get(SongAsset, song_id)
    if existing is None:
        existing = SongAsset(
            id=song_id,
            title=title,
            artist=artist,
            duration_ms=duration_ms,
            lead_vocal_path=str(lead_vocal_path),
            instrumental_path=str(instrumental_path),
        )
        db.add(existing)
    else:
        existing.title = title
        existing.artist = artist
        existing.duration_ms = duration_ms
        existing.lead_vocal_path = str(lead_vocal_path)
        existing.instrumental_path = str(instrumental_path)
    await db.commit()
    await db.refresh(existing)
    return existing


async def _run_separation_job(
    job: _SeparationJobRecord,
    raw_path: Path,
    storage_root: Path,
) -> None:
    def _on_progress(pct: int, stage: str) -> None:
        job.progress = max(job.progress, min(99, pct))
        job.stage = stage
        job.touch()

    try:
        async with _SEPARATION_SEMAPHORE:
            job.status = "running"
            job.progress = 10
            job.stage = "准备开始人声/伴奏分离"
            job.touch()
            logger.info("异步曲库分离任务开始 job_id=%s song_id=%s title=%r", job.id, job.song_id, job.title)

            res = await asyncio.to_thread(
                separate_song_sync,
                raw_path,
                job.song_id,
                storage_root,
                _on_progress,
            )

            async with AsyncSessionLocal() as db:
                await _upsert_song_asset(
                    db,
                    song_id=job.song_id,
                    title=job.title,
                    artist=job.artist,
                    duration_ms=res.duration_ms,
                    lead_vocal_path=res.lead_vocal_path,
                    instrumental_path=res.instrumental_path,
                )

            job.status = "completed"
            job.progress = 100
            job.stage = "分离完成并已入库"
            job.duration_ms = res.duration_ms
            job.touch()
            logger.info(
                "异步曲库分离任务完成 job_id=%s song_id=%s duration_ms=%d",
                job.id,
                job.song_id,
                res.duration_ms,
            )
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.stage = "分离失败"
        job.error_message = str(exc)
        job.touch()
        logger.exception("异步曲库分离任务失败 job_id=%s song_id=%s", job.id, job.song_id)
    finally:
        raw_path.unlink(missing_ok=True)


@router.get("", response_model=list[SongOut])
async def list_songs(db: DbSession) -> list[SongAsset]:
    result = await db.execute(select(SongAsset))
    songs = list(result.scalars().all())
    songs.sort(key=_song_sort_key)
    return songs


@router.get("/next-id")
async def get_next_song_id(db: DbSession) -> dict[str, str]:
    return {"next_song_id": await _compute_next_song_id(db)}


@router.post("/separate/jobs", response_model=SongSeparationJobOut, status_code=202)
async def create_separation_job(
    db: DbSession,
    file: UploadFile = File(...),
    song_id: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    artist: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    """提交异步 BS-RoFormer 人声/伴奏分离任务，立即返回 job_id 供前端轮询/SSE 跟踪。"""
    raw_sid = (song_id or "").strip()
    if raw_sid:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_sid)
    else:
        safe_id = await _compute_next_song_id(db)

    if not safe_id:
        raise HTTPException(status_code=400, detail="song_id 不能为空")

    parsed_artist, parsed_title = parse_song_filename(file.filename)
    final_title = (title or "").strip() or parsed_title
    final_artist = (artist or "").strip() or parsed_artist

    storage_root = get_storage_root()
    upload_dir = (storage_root / "uploads").resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "song.wav").suffix or ".wav"
    job_id = f"sep_{uuid4().hex[:8]}"
    raw_path = upload_dir / f"{job_id}_{safe_id}_raw{suffix}"
    raw_path.write_bytes(await file.read())

    job = _SeparationJobRecord(
        id=job_id,
        song_id=safe_id,
        title=final_title,
        artist=final_artist,
        status="queued",
        progress=5,
        stage="文件已上传，等待分离队列",
    )
    _SEPARATION_JOBS[job_id] = job

    task = asyncio.create_task(_run_separation_job(job, raw_path, storage_root))
    _SEPARATION_TASKS.add(task)
    task.add_done_callback(_SEPARATION_TASKS.discard)

    return job.to_dict()


@router.get("/separate/jobs", response_model=list[SongSeparationJobOut])
async def list_separation_jobs() -> list[dict[str, object]]:
    jobs = sorted(_SEPARATION_JOBS.values(), key=lambda j: j.created_at, reverse=True)
    return [j.to_dict() for j in jobs[:30]]


@router.get("/separate/jobs/{job_id}", response_model=SongSeparationJobOut)
async def get_separation_job(job_id: str) -> dict[str, object]:
    job = _SEPARATION_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分离任务不存在")
    return job.to_dict()


@router.get("/separate/jobs/{job_id}/stream")
async def stream_separation_job(job_id: str, request: Request) -> StreamingResponse:
    job = _SEPARATION_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="分离任务不存在")

    async def event_generator() -> AsyncGenerator[str, None]:
        last_signature: tuple[str, int, str, str | None] | None = None
        last_heartbeat = time.monotonic()

        try:
            while True:
                if await request.is_disconnected():
                    break
                sig = (job.status, job.progress, job.stage, job.error_message)
                if sig != last_signature:
                    last_signature = sig
                    yield f"data: {json.dumps(job.to_dict(), ensure_ascii=False)}\n\n"
                    last_heartbeat = time.monotonic()
                    if job.status in {"completed", "failed"}:
                        break
                elif time.monotonic() - last_heartbeat >= 15.0:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(0.35)
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


@router.post("/separate", response_model=SongOut)
async def separate_and_register_song(
    db: DbSession,
    song_id: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    artist: Annotated[str | None, Form()] = None,
    file: UploadFile = File(...),
) -> SongAsset:
    """兼容旧版同步接口：上传原曲并调用 BS-RoFormer 分离为 lead_vocal.wav 与 instrumental.opus。"""
    raw_sid = (song_id or "").strip()
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_sid) if raw_sid else await _compute_next_song_id(db)
    if not safe_id:
        raise HTTPException(status_code=400, detail="song_id 不能为空")

    parsed_artist, parsed_title = parse_song_filename(file.filename)
    final_title = (title or "").strip() or parsed_title
    final_artist = (artist or "").strip() or parsed_artist

    storage_root = get_storage_root()
    upload_dir = (storage_root / "uploads").resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "song.wav").suffix or ".wav"
    raw_path = upload_dir / f"{safe_id}_raw{suffix}"
    raw_path.write_bytes(await file.read())

    try:
        res = await asyncio.to_thread(separate_song_sync, raw_path, safe_id, storage_root)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        raw_path.unlink(missing_ok=True)

    return await _upsert_song_asset(
        db,
        song_id=safe_id,
        title=final_title,
        artist=final_artist,
        duration_ms=res.duration_ms,
        lead_vocal_path=res.lead_vocal_path,
        instrumental_path=res.instrumental_path,
    )


@router.get("/{song_id}/instrumental")
async def get_instrumental(
    song_id: str,
    request: Request,
    db: DbSession,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    storage_root = get_storage_root()
    song = await db.get(SongAsset, song_id)

    if song is not None:
        inst_path = Path(song.instrumental_path)
        if not inst_path.is_absolute():
            inst_path = (storage_root / inst_path).resolve()
    else:
        # 兼容直接按 storage/songs/{song_id}/instrumental.* 约定读取
        song_dir = (storage_root / "songs" / song_id).resolve()
        inst_path = song_dir / "instrumental.opus"
        if not inst_path.is_file():
            inst_path = song_dir / "instrumental.wav"

    if not inst_path.is_file():
        raise HTTPException(status_code=404, detail="伴奏文件不存在")

    etag = _compute_etag(inst_path)
    cache_headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
        "Accept-Ranges": "bytes",
    }

    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=304, headers=cache_headers)

    file_size = inst_path.stat().st_size
    media_type = _guess_media_type(inst_path)
    range_header = request.headers.get("range")

    if not range_header:
        return StreamingResponse(
            _iter_file_range(inst_path, 0, file_size - 1),
            status_code=200,
            media_type=media_type,
            headers={**cache_headers, "Content-Length": str(file_size)},
        )

    match = RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="无效的 Range 请求头")

    start_str, end_str = match.groups()
    if not start_str and not end_str:
        raise HTTPException(status_code=416, detail="无效的 Range 区间")

    if start_str:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
    else:
        suffix_len = int(end_str)
        start = max(0, file_size - suffix_len)
        end = file_size - 1

    if start >= file_size or end >= file_size or start > end:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1
    headers = {
        **cache_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(content_length),
    }
    return StreamingResponse(
        _iter_file_range(inst_path, start, end),
        status_code=206,
        media_type=media_type,
        headers=headers,
    )
