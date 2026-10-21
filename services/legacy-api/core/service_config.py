"""全局服务配置（后端级键值存储）。

把"启动服务的先决条件"类密钥（如快递100 的 KUAIDI100_KEY /
KUAIDI100_CUSTOMER）从 `.env` 文件迁移到后台可配置，避免改文件 + 重启。

取值优先级：后台 DB 中已配置的值 > 环境变量。一旦在后台配置，即覆盖
环境变量（除非在后台把该配置项清空）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .db import get_main_db

logger = logging.getLogger(__name__)

_TABLE = "service_configs"


async def _ensure_table(db) -> None:
    await db.execute(
        f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )"""
    )
    await db.commit()


async def get_service_config(key: str, default: str = "") -> str:
    db = await get_main_db()
    await _ensure_table(db)
    cursor = await db.execute(f"SELECT value FROM {_TABLE} WHERE key = ?", (key,))
    row = await cursor.fetchone()
    if not row or row[0] is None:
        return default
    return row[0]


async def set_service_config(key: str, value: str) -> None:
    db = await get_main_db()
    await _ensure_table(db)
    now = datetime.now().isoformat()
    await db.execute(
        f"INSERT INTO {_TABLE} (key, value, updated_at) VALUES (?, ?, ?) "
        f"ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now),
    )
    await db.commit()


def mask_secret(value: str) -> str:
    """把密钥脱敏展示：保留首尾，中间用 **** 替代。"""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) >= 6:
        return f"{v[:2]}****{v[-4:]}"
    if len(v) >= 2:
        return f"{v[0]}****{v[-1]}"
    return "****"
