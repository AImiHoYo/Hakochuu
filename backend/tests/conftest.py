"""Pytest 全局夹具：在导入 app 前重定向数据库到临时 SQLite，避免污染 storage/hakochuu.db。"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="hakochuu_pytest_db_"))
os.environ["HAKOCHUU_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_DIR / 'test.db'}"


@atexit.register
def _cleanup_test_db_dir() -> None:
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)
