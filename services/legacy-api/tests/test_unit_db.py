from __future__ import annotations

import asyncio
import os
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from core import db as db_mod


def test_resolve_db_path_honors_absolute_config(monkeypatch, tmp_path):
    configured = str(tmp_path / "persistent" / "inksight.db")
    monkeypatch.setenv("TEST_DB_PATH", configured)
    assert db_mod._resolve_db_path("TEST_DB_PATH", "fallback.db") == os.path.normpath(configured)


def test_resolve_db_path_resolves_relative_config_from_backend(monkeypatch):
    monkeypatch.setenv("TEST_DB_PATH", "data/inksight.db")
    assert db_mod._resolve_db_path("TEST_DB_PATH", "fallback.db") == os.path.join(
        db_mod._DB_DIR, "data", "inksight.db"
    )


def test_select_main_db_path_falls_back_when_configured_db_has_no_devices(monkeypatch, tmp_path):
    configured = tmp_path / "data" / "inksight.db"
    configured.parent.mkdir()
    with sqlite3.connect(configured) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO users DEFAULT VALUES")

    legacy = tmp_path / "inksight.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE configs (id INTEGER PRIMARY KEY, mac TEXT)")
        conn.execute("INSERT INTO configs (mac) VALUES ('7C:4F:AD:24:66:9C')")

    monkeypatch.setattr(db_mod, "_DB_DIR", str(tmp_path))
    assert db_mod._select_main_db_path(str(configured)) == str(legacy)


def test_select_main_db_path_falls_back_when_configured_db_only_has_membership(monkeypatch, tmp_path):
    configured = tmp_path / "data" / "inksight.db"
    configured.parent.mkdir()
    with sqlite3.connect(configured) as conn:
        conn.execute("CREATE TABLE device_memberships (id INTEGER PRIMARY KEY, mac TEXT)")
        conn.execute("INSERT INTO device_memberships (mac) VALUES ('7C:4F:AD:24:66:9C')")

    legacy = tmp_path / "inksight.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE configs (id INTEGER PRIMARY KEY, mac TEXT)")
        conn.execute("INSERT INTO configs (mac) VALUES ('AA:BB:CC:DD:EE:FF')")

    monkeypatch.setattr(db_mod, "_DB_DIR", str(tmp_path))
    assert db_mod._select_main_db_path(str(configured)) == str(legacy)


def test_select_main_db_path_keeps_configured_db_when_it_has_configs(monkeypatch, tmp_path):
    configured = tmp_path / "data" / "inksight.db"
    configured.parent.mkdir()
    with sqlite3.connect(configured) as conn:
        conn.execute("CREATE TABLE configs (id INTEGER PRIMARY KEY, mac TEXT)")
        conn.execute("INSERT INTO configs (mac) VALUES ('7C:4F:AD:24:66:9C')")

    legacy = tmp_path / "inksight.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE configs (id INTEGER PRIMARY KEY, mac TEXT)")
        conn.execute("INSERT INTO configs (mac) VALUES ('AA:BB:CC:DD:EE:FF')")

    monkeypatch.setattr(db_mod, "_DB_DIR", str(tmp_path))
    assert db_mod._select_main_db_path(str(configured)) == str(configured)


@pytest.fixture(autouse=True)
async def _cleanup_db_singletons():
    await db_mod.close_all()
    yield
    await db_mod.close_all()


@pytest.mark.asyncio
async def test_get_main_db_reuses_singleton_connection():
    conn = AsyncMock()
    conn._closed = False
    conn.execute = AsyncMock(return_value=AsyncMock())

    with patch.object(db_mod, "_open_db", new=AsyncMock(return_value=conn)) as mock_open:
        first = await db_mod.get_main_db()
        second = await db_mod.get_main_db()

    assert first is conn
    assert second is conn
    assert mock_open.await_count == 1


@pytest.mark.asyncio
async def test_get_main_db_reopens_when_existing_singleton_is_unhealthy(monkeypatch):
    stale = AsyncMock()
    stale._closed = False
    stale.execute = AsyncMock(side_effect=RuntimeError("broken"))
    stale.close = AsyncMock()

    fresh = AsyncMock()
    fresh._closed = False
    fresh.execute = AsyncMock(return_value=AsyncMock())

    monkeypatch.setattr(db_mod, "_main_conn", stale, raising=False)

    with patch.object(db_mod, "_open_db", new=AsyncMock(return_value=fresh)) as mock_open:
        result = await db_mod.get_main_db()

    assert result is fresh
    stale.close.assert_awaited_once()
    assert mock_open.await_count == 1


@pytest.mark.asyncio
async def test_get_main_db_recreates_state_for_new_event_loop():
    first = AsyncMock()
    first._closed = False
    first.execute = AsyncMock(return_value=AsyncMock())

    second = AsyncMock()
    second._closed = False
    second.execute = AsyncMock(return_value=AsyncMock())

    original_loop = asyncio.get_running_loop()

    class OtherLoop:
        pass

    other_loop = OtherLoop()

    with patch.object(db_mod, "_open_db", new=AsyncMock(side_effect=[first, second])) as mock_open:
        same_loop_conn = await db_mod.get_main_db()
        assert same_loop_conn is first

        with patch("core.db.asyncio.get_running_loop", side_effect=[other_loop, other_loop]):
            new_loop_conn = await db_mod.get_main_db()

    assert new_loop_conn is second
    assert mock_open.await_count == 2
