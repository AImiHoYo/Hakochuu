from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UploadCheckRequest(BaseModel):
    hashes: list[str] = Field(default_factory=list)
    kind: Literal["speech", "sing", "song", "avatar"] = "speech"
    local_paths: list[str] | None = None


class UploadCheckResponse(BaseModel):
    existing: list[str]
    missing: list[str]
    paths: dict[str, str]


class UploadAssetResponse(BaseModel):
    sha256: str
    path: str
    size: int
    kind: str


class CharacterRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    ref_speech_path: str | None = None
    ref_speech_text: str | None = None
    ref_sing_path: str | None = None
    avatar_type: Literal["none", "live2d", "vrm", "webrtc_video"] = "none"
    avatar_asset_url: str | None = None
    lip_sync_mode: Literal["rms", "viseme"] = "rms"


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    ref_speech_path: str | None
    ref_speech_text: str | None
    ref_sing_path: str | None
    avatar_type: str
    avatar_asset_url: str | None
    lip_sync_mode: str
    created_at: datetime
