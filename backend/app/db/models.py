from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class CharacterProfile(Base):
    __tablename__ = "character_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 引导录音与音色资产
    ref_speech_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ref_speech_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_sing_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    speaker_embedding_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 后台微调权重（可选增强）
    tts_lora_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vc_checkpoint_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 预留虚拟形象字段
    avatar_type: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    avatar_asset_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lip_sync_mode: Mapped[str] = mapped_column(String(32), default="rms", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SongAsset(Base):
    __tablename__ = "songs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(256), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lead_vocal_path: Mapped[str] = mapped_column(String(512), nullable=False)
    instrumental_path: Mapped[str] = mapped_column(String(512), nullable=False)
    lyrics_lrc: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FinetuneJob(Base):
    __tablename__ = "finetune_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    character_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_engine: Mapped[str] = mapped_column(String(32), nullable=False)  # qwen3_tts / seed_vc
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
