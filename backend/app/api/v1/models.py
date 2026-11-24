from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.model_registry import get_llm_config, list_llm_configs

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/llms")
async def get_llm_models() -> list[dict[str, Any]]:
    return list_llm_configs()


@router.get("/llms/{model_id}")
async def get_llm_model_detail(model_id: str) -> dict[str, Any]:
    cfg = get_llm_config(model_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="未找到该 LLM 模型配置")
    return cfg
