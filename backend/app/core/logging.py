"""统一结构化日志工具（时间戳 + 级别 + 模块名）。"""

from __future__ import annotations

import logging
import os
import sys

LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

_ROOT_LOGGER_NAME = "hakochuu"
_CONFIGURED = False


def setup_logging(service_name: str | None = None) -> logging.Logger:
    """初始化 hakochuu 统一日志格式，避免与 uvicorn root logger 重复输出。"""
    global _CONFIGURED
    level_name = os.getenv("HAKOCHUU_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
    root_logger.setLevel(level)
    root_logger.propagate = False

    if not _CONFIGURED or not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT))
        root_logger.handlers.clear()
        root_logger.addHandler(handler)
        _CONFIGURED = True

    if service_name:
        return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{service_name}")
    return root_logger


def get_logger(module_name: str) -> logging.Logger:
    """按模块名获取 logger（自动挂载到 hakochuu 命名空间）。"""
    setup_logging()
    clean = module_name.removeprefix("app.")
    if clean.startswith(f"{_ROOT_LOGGER_NAME}."):
        return logging.getLogger(clean)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{clean}")
