import asyncio
import json
import os
from collections.abc import AsyncGenerator
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CharacterProfile, FinetuneJob
from app.db.session import get_db

router = APIRouter(prefix="/finetune", tags=["finetune"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


class FinetuneJobCreate(BaseModel):
    character_id: str
    target_engine: Literal["qwen3_tts", "gpt_sovits", "seed_vc"] = "qwen3_tts"


class FinetuneJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    character_id: str
    target_engine: str
    status: str
    progress: int
    error_message: str | None


@router.post("/jobs", response_model=FinetuneJobOut, status_code=201)
async def create_finetune_job(
    req: FinetuneJobCreate,
    db: DbSession,
) -> FinetuneJob:
    char = await db.get(CharacterProfile, req.character_id)
    if char is None:
        raise HTTPException(status_code=404, detail="角色不存在，无法创建微调任务")

    job = FinetuneJob(
        id=f"job_{uuid4().hex[:8]}",
        character_id=req.character_id,
        target_engine=req.target_engine,
        status="queued",
        progress=0,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/jobs", response_model=list[FinetuneJobOut])
async def list_finetune_jobs(
    db: DbSession,
    character_id: str | None = None,
) -> list[FinetuneJob]:
    stmt = select(FinetuneJob).order_by(FinetuneJob.created_at.desc())
    if character_id:
        stmt = stmt.where(FinetuneJob.character_id == character_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/jobs/{job_id}", response_model=FinetuneJobOut)
async def get_finetune_job(job_id: str, db: DbSession) -> FinetuneJob:
    job = await db.get(FinetuneJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="微调任务不存在")
    return job


@router.get("/jobs/{job_id}/stream")
async def stream_finetune_job(job_id: str, db: DbSession) -> StreamingResponse:
    job = await db.get(FinetuneJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="微调任务不存在")

    mock_engines = os.getenv("HAKOCHUU_MOCK_ENGINES", "true").lower() == "true"

    async def event_generator() -> AsyncGenerator[str, None]:
        if mock_engines and job.status != "completed":
            for pct, status in [(25, "running"), (65, "running"), (100, "completed")]:
                job.progress = pct
                job.status = status
                await db.commit()
                payload = {
                    "id": job.id,
                    "character_id": job.character_id,
                    "target_engine": job.target_engine,
                    "status": status,
                    "progress": pct,
                }
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(0.05)
            return

        payload = {
            "id": job.id,
            "character_id": job.character_id,
            "target_engine": job.target_engine,
            "status": job.status,
            "progress": job.progress,
        }
        yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
