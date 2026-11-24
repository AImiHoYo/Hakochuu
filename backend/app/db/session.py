import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base


def get_database_url() -> str:
    default_db = Path(__file__).resolve().parents[3] / "storage" / "hakochuu.db"
    default_db.parent.mkdir(parents=True, exist_ok=True)
    return os.getenv("HAKOCHUU_DATABASE_URL", f"sqlite+aiosqlite:///{default_db}")


engine = create_async_engine(get_database_url(), echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
