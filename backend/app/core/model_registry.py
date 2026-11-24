import os
from pathlib import Path
from typing import Any

import yaml


def get_llm_registry_dir() -> Path:
    raw = os.getenv("HAKOCHUU_LLM_REGISTRY_DIR")
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parents[3] / "shared" / "llm_models"


def list_llm_configs() -> list[dict[str, Any]]:
    """扫描 shared/llm_models/*/model.yaml，返回所有可用 LLM 及其专属参数定义。"""
    reg_dir = get_llm_registry_dir()
    if not reg_dir.is_dir():
        return []

    items: list[dict[str, Any]] = []
    for yaml_path in sorted(reg_dir.glob("*/model.yaml")):
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if isinstance(data, dict) and data.get("id"):
            items.append(data)
    return items


def get_llm_config(model_id: str) -> dict[str, Any] | None:
    for item in list_llm_configs():
        if item.get("id") == model_id:
            return item
        served = (item.get("deployment") or {}).get("served_model_name")
        if served and served == model_id:
            return item
    return None


def get_default_llm_id() -> str | None:
    """优先环境变量，其次 registry 中第一个可用模型。"""
    env_id = (os.getenv("HAKOCHUU_LLM_MODEL") or "").strip()
    if env_id:
        matched = get_llm_config(env_id)
        if matched is not None:
            return str(matched.get("id") or env_id)
        return env_id
    configs = list_llm_configs()
    if configs:
        return str(configs[0]["id"])
    return None


def build_system_prompt(
    model_cfg: dict[str, Any] | None,
    llm_params: dict[str, Any] | None = None,
    character_name: str | None = None,
    character_description: str | None = None,
) -> str:
    """将共享角色库设定与当前 LLM 专属场景参数合并为最终 System Prompt。"""
    parts: list[str] = []
    if character_name:
        char_line = f"你的角色名称是“{character_name}”。"
        if character_description:
            char_line += f"角色设定：{character_description}"
        parts.append(char_line)

    params = llm_params or {}
    if model_cfg:
        for param_def in model_cfg.get("custom_params", []):
            key = param_def.get("key")
            if key == "social_scene":
                selected = params.get("social_scene") or param_def.get("default")
                for opt in param_def.get("options", []):
                    if opt.get("value") == selected and opt.get("system_prompt"):
                        parts.append(str(opt["system_prompt"]))
                        break
            elif key == "current_topic":
                topic = str(params.get("current_topic") or "").strip()
                if topic:
                    parts.append(f"当前话题：【{topic}】。")

    parts.append("请在回复开头附带情绪标签（例如 [emotion:neutral] 或 [emotion:cheerful]）。")
    return "\n".join(parts)
