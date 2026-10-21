"""BYOK Key 的三态语义与掩码保护。

背景：GET /api/user/profile 只返回掩码（sk-a****1234）。前端如果把掩码回填进
输入框，用户点保存就会用掩码覆盖真 Key，设备端随即报 "API key 无效"。
所以约定：
  * 请求体不带 api_key      → 保持库里已有 Key 不变
  * api_key == ""           → 清空
  * api_key == "<真 Key>"   → 覆盖
  * api_key 里含 "****"      → 直接 400，拒绝写入
"""

from __future__ import annotations

import secrets

import pytest
from httpx import AsyncClient

from api.index import app
from core.config_store import get_user_llm_config, init_db
from core.db import get_main_db
from core.stats_store import init_stats_db
from core.cache import init_cache_db


@pytest.fixture
async def client(tmp_path):
    from core import db as db_mod

    await db_mod.close_all()

    async def _normalize_configs_table():
        db = await get_main_db()
        cur = await db.execute("PRAGMA table_info(configs)")
        cols = {row[1] for row in await cur.fetchall()}

        async def _add(col: str, ddl: str):
            if col not in cols:
                await db.execute(f"ALTER TABLE configs ADD COLUMN {col} {ddl}")

        await _add("focus_listening", "INTEGER DEFAULT 0")
        await _add("latitude", "REAL")
        await _add("longitude", "REAL")
        await _add("timezone", "TEXT DEFAULT ''")
        await _add("admin1", "TEXT DEFAULT ''")
        await _add("country", "TEXT DEFAULT ''")
        await _add("is_active", "INTEGER DEFAULT 1")
        await db.commit()

    test_main_db = str(tmp_path / "test_inksight.db")
    test_cache_db = str(tmp_path / "test_cache.db")

    from unittest.mock import patch

    with patch.object(db_mod, "_MAIN_DB_PATH", test_main_db), \
         patch.object(db_mod, "_CACHE_DB_PATH", test_cache_db), \
         patch("core.config_store.DB_PATH", test_main_db), \
         patch("core.stats_store.DB_PATH", test_main_db), \
         patch("core.cache._CACHE_DB_PATH", test_cache_db):
        await init_db()
        await init_stats_db()
        await init_cache_db()
        await _normalize_configs_table()

        from httpx import ASGITransport  # type: ignore

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

        await db_mod.close_all()


async def _register_and_login(client: AsyncClient) -> dict:
    username = f"byok_key_{secrets.token_hex(4)}"
    password = "pass1234"
    reg = await client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "email": f"{username}@example.com"},
    )
    assert reg.status_code in (200, 409)
    login = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    return {"user_id": int(me.json()["user_id"]), "headers": headers}


async def _cleanup_user_data(user_id: int) -> None:
    db = await get_main_db()
    try:
        await db.execute("BEGIN")
        await db.execute("DELETE FROM user_llm_config WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM api_quotas WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    except Exception:
        await db.rollback()


def _body(**overrides) -> dict:
    body = {
        "llm_access_mode": "preset",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "",
        "image_provider": "aliyun",
        "image_model": "",
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_profile_returns_mask_and_key_set_flag(client: AsyncClient):
    auth = await _register_and_login(client)
    headers = {**auth["headers"], "Content-Type": "application/json"}
    try:
        real_key = "sk-real-secret-key-0123456789abcdef"
        put = await client.put(
            "/api/user/profile/llm",
            headers=headers,
            json=_body(api_key=real_key, image_api_key="sk-img-secret-0123456789"),
        )
        assert put.status_code == 200

        prof = await client.get("/api/user/profile", headers=auth["headers"])
        cfg = prof.json()["llm_config"]
        assert "****" in cfg["api_key"]
        assert real_key not in cfg["api_key"]
        assert cfg["llm_api_key_set"] is True
        assert cfg["image_api_key_set"] is True
    finally:
        await _cleanup_user_data(auth["user_id"])


@pytest.mark.asyncio
async def test_omitting_api_key_keeps_existing_key(client: AsyncClient):
    """核心回归：不带 api_key 的保存（前端"留空=不修改"）不能毁掉已有 Key。"""
    auth = await _register_and_login(client)
    headers = {**auth["headers"], "Content-Type": "application/json"}
    try:
        real_key = "sk-real-secret-key-0123456789abcdef"
        ok = await client.put("/api/user/profile/llm", headers=headers, json=_body(api_key=real_key))
        assert ok.status_code == 200

        # 模拟用户只改了 model，Key 输入框留空
        ok2 = await client.put(
            "/api/user/profile/llm",
            headers=headers,
            json=_body(model="deepseek-reasoner"),
        )
        assert ok2.status_code == 200

        stored = await get_user_llm_config(auth["user_id"])
        assert stored is not None
        assert stored.get("api_key") == real_key
        assert stored.get("model") == "deepseek-reasoner"
    finally:
        await _cleanup_user_data(auth["user_id"])


@pytest.mark.asyncio
async def test_masked_key_is_rejected(client: AsyncClient):
    """把掩码回存必须被拒绝，而不是写坏库。"""
    auth = await _register_and_login(client)
    headers = {**auth["headers"], "Content-Type": "application/json"}
    try:
        real_key = "sk-real-secret-key-0123456789abcdef"
        assert (await client.put("/api/user/profile/llm", headers=headers, json=_body(api_key=real_key))).status_code == 200

        masked = await client.get("/api/user/profile", headers=auth["headers"])
        masked_key = masked.json()["llm_config"]["api_key"]
        assert "****" in masked_key

        bad = await client.put("/api/user/profile/llm", headers=headers, json=_body(api_key=masked_key))
        assert bad.status_code == 400

        # 真 Key 没被污染
        stored = await get_user_llm_config(auth["user_id"])
        assert stored is not None
        assert stored.get("api_key") == real_key
    finally:
        await _cleanup_user_data(auth["user_id"])


@pytest.mark.asyncio
async def test_empty_string_clears_key(client: AsyncClient):
    auth = await _register_and_login(client)
    headers = {**auth["headers"], "Content-Type": "application/json"}
    try:
        real_key = "sk-real-secret-key-0123456789abcdef"
        assert (await client.put("/api/user/profile/llm", headers=headers, json=_body(api_key=real_key))).status_code == 200
        assert (await client.put("/api/user/profile/llm", headers=headers, json=_body(api_key=""))).status_code == 200

        stored = await get_user_llm_config(auth["user_id"])
        assert stored is not None
        assert (stored.get("api_key") or "") == ""
    finally:
        await _cleanup_user_data(auth["user_id"])
