import shutil
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assets import get_storage_root
from app.db.models import CharacterProfile, FinetuneJob
from app.db.session import get_db
from app.schemas.character import CharacterOut, CharacterRegisterRequest

router = APIRouter(prefix="/characters", tags=["characters"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _adopt_asset(
    storage_root: Path,
    char_dir: Path,
    source_rel_or_abs: str | None,
    name: str,
) -> str | None:
    if not source_rel_or_abs:
        return None
    src = Path(source_rel_or_abs)
    if not src.is_absolute():
        src = (storage_root / source_rel_or_abs).resolve()
    else:
        src = src.resolve()

    if not src.is_file():
        return source_rel_or_abs

    ext = src.suffix or ".wav"
    dest = char_dir / f"{name}{ext}"
    if src != dest:
        shutil.copy2(src, dest)
    return str(dest.relative_to(storage_root))


@router.post("/register", response_model=CharacterOut, status_code=201)
async def register_character(
    req: CharacterRegisterRequest,
    db: DbSession,
) -> CharacterProfile:
    char_id = f"char_{uuid4().hex[:8]}"
    storage_root = get_storage_root()
    char_dir = storage_root / "characters" / char_id
    char_dir.mkdir(parents=True, exist_ok=True)

    speech_path = _adopt_asset(storage_root, char_dir, req.ref_speech_path, "ref_speech")
    sing_path = _adopt_asset(storage_root, char_dir, req.ref_sing_path, "ref_sing")

    profile = CharacterProfile(
        id=char_id,
        name=req.name,
        description=req.description,
        ref_speech_path=speech_path,
        ref_speech_text=req.ref_speech_text,
        ref_sing_path=sing_path,
        avatar_type=req.avatar_type,
        avatar_asset_url=req.avatar_asset_url,
        lip_sync_mode=req.lip_sync_mode,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.get("", response_model=list[CharacterOut])
async def list_characters(db: DbSession) -> list[CharacterProfile]:
    result = await db.execute(select(CharacterProfile).order_by(CharacterProfile.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{char_id}", response_model=CharacterOut)
async def get_character(char_id: str, db: DbSession) -> CharacterProfile:
    profile = await db.get(CharacterProfile, char_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return profile


@router.delete("/{char_id}")
async def delete_character(char_id: str, db: DbSession) -> dict[str, str]:
    profile = await db.get(CharacterProfile, char_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    await db.execute(delete(FinetuneJob).where(FinetuneJob.character_id == char_id))
    await db.delete(profile)
    await db.commit()
    char_dir = (get_storage_root() / "characters" / char_id).resolve()
    shutil.rmtree(char_dir, ignore_errors=True)
    return {"status": "deleted", "id": char_id}
