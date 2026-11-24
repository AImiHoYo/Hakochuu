from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.logging import setup_logging
from app.db.session import init_db
from app.ws.session import router as ws_router

logger = setup_logging("gateway")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Hakochuu Gateway 启动中，初始化数据库...")
    await init_db()
    logger.info("Hakochuu Gateway 就绪")
    yield
    logger.info("Hakochuu Gateway 已关闭")


app = FastAPI(
    title="Hakochuu Gateway",
    version="0.1.0",
    description="Low-latency AI Voice Chat, Singing & Avatar WebUI Backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)
app.include_router(ws_router)
