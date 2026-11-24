from fastapi import APIRouter

from app.api.v1 import assets, characters, finetune, logs, models, songs, system

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(system.router)
api_v1_router.include_router(assets.router)
api_v1_router.include_router(characters.router)
api_v1_router.include_router(songs.router)
api_v1_router.include_router(finetune.router)
api_v1_router.include_router(models.router)
api_v1_router.include_router(logs.router)
