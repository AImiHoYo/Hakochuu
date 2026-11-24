import hashlib
import os
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas.character import (
    UploadAssetResponse,
    UploadCheckRequest,
    UploadCheckResponse,
)

router = APIRouter(prefix="/assets", tags=["assets"])


def get_storage_root() -> Path:
    raw = os.getenv("HAKOCHUU_STORAGE_DIR")
    root = Path(raw).resolve() if raw else Path(__file__).resolve().parents[4] / "storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)
    return cleaned or "asset.bin"


def _find_uploaded_by_sha256(uploads_dir: Path, sha256: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        return None
    matches = list(uploads_dir.glob(f"{sha256.lower()}_*"))
    return matches[0] if matches else None


@router.post("/upload-check", response_model=UploadCheckResponse)
async def upload_check(req: UploadCheckRequest) -> UploadCheckResponse:
    storage_root = get_storage_root()
    uploads_dir = storage_root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    existing: list[str] = []
    missing: list[str] = []
    paths: dict[str, str] = {}

    for h in req.hashes:
        norm = h.lower()
        found = _find_uploaded_by_sha256(uploads_dir, norm)
        if found is not None and found.is_file():
            existing.append(norm)
            paths[norm] = str(found.relative_to(storage_root))
        else:
            missing.append(norm)

    # 本地全栈模式允许直接校验并登记本地路径（零网络拷贝）
    deploy_mode = os.getenv("HAKOCHUU_DEPLOY_MODE", "local")
    if deploy_mode == "local" and req.local_paths:
        for lp in req.local_paths:
            resolved = Path(lp).expanduser().resolve()
            if resolved.is_file():
                paths[lp] = str(resolved)

    return UploadCheckResponse(existing=existing, missing=missing, paths=paths)


@router.post("/upload", response_model=UploadAssetResponse)
async def upload_asset(
    file: Annotated[UploadFile, File(...)],
    sha256: Annotated[str | None, Form()] = None,
    kind: Annotated[str, Form()] = "speech",
    character_id: Annotated[str | None, Form()] = None,
) -> UploadAssetResponse:
    storage_root = get_storage_root()
    uploads_dir = storage_root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    actual_sha = hashlib.sha256(content).hexdigest()
    if sha256 and sha256.lower() != actual_sha:
        raise HTTPException(status_code=400, detail="SHA-256 校验失败")

    safe_name = _safe_filename(file.filename or "audio.wav")
    target = (uploads_dir / f"{actual_sha}_{safe_name}").resolve()
    if uploads_dir.resolve() not in target.parents:
        raise HTTPException(status_code=400, detail="非法文件名")

    target.write_bytes(content)
    rel_path = str(target.relative_to(storage_root))
    return UploadAssetResponse(
        sha256=actual_sha,
        path=rel_path,
        size=len(content),
        kind=kind,
    )
