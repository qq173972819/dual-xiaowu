"""设备绑定态 → 屏幕内容，以及 LLM Key 取值链路的回归测试。

覆盖三件以前会出错的事：
1. 未绑定的彩色设备必须拿到**配对码帧**（且是设备能直接吃的定长 raw，
   不是 BMP —— 六色固件认 BMP 会判失败再回落一次，白白多跑一个 45s 周期）。
2. /api/render 没带 mac 时**不许**再拿平台 Key 去生成内容：那会让设备刷出
   一张「API key 无效或已过期」，而真实原因只是它没上报 MAC。
3. 账号选了「平台免费额度」但服务端 .env 没配 Key 时，用账号里存着的 Key 兜底。
"""
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MAC = "AA:BB:CC:DD:EE:02"


@pytest.fixture
async def isolated_db(tmp_path):
    from core import config_store, db as db_mod, stats_store

    db_path = str(tmp_path / "test.db")
    await db_mod.close_all()
    with patch.object(db_mod, "_MAIN_DB_PATH", db_path), \
         patch.object(config_store, "DB_PATH", db_path), \
         patch.object(stats_store, "DB_PATH", db_path):
        await config_store.init_db()
        yield db_path
    await db_mod.close_all()


def _client():
    from fastapi.testclient import TestClient

    from api.index import app

    return TestClient(app)


def test_unbound_six_color_device_gets_packed_pairing_frame(isolated_db):
    """未绑定 + colors=6/bpp=4 → 192000B 定长 4bpp raw（不是 BMP）。"""
    client = _client()
    token = client.post(f"/api/device/{MAC}/token").json()["token"]
    resp = client.get(
        f"/api/render?mac={MAC}&w=800&h=480&bpp=4&colors=6",
        headers={"X-Device-Token": token},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert len(resp.content) == 800 * 480 // 2, "4bpp 帧必须是 192000 字节，固件按定长读取"
    assert resp.headers.get("X-Device-Unbound") == "1"


def test_unbound_four_color_device_gets_packed_2bpp_frame(isolated_db):
    client = _client()
    token = client.post(f"/api/device/{MAC}/token").json()["token"]
    resp = client.get(
        f"/api/render?mac={MAC}&w=800&h=480&bpp=2&colors=4",
        headers={"X-Device-Token": token},
    )
    assert resp.status_code == 200
    assert len(resp.content) == 800 * 480 // 4


def test_unbound_monochrome_device_still_gets_bmp(isolated_db):
    """黑白老固件（不带 colors/bpp）保持 BMP，别破坏既有设备。"""
    client = _client()
    token = client.post(f"/api/device/{MAC}/token").json()["token"]
    resp = client.get(
        f"/api/render?mac={MAC}&w=400&h=300", headers={"X-Device-Token": token}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/bmp"


def test_render_without_mac_never_generates_content(isolated_db):
    """没有 MAC → 引导图，且**绝不**调用 build_image（不会再刷出 API key 废图）。"""
    client = _client()
    with patch("api.routes.render.build_image", new=AsyncMock()) as mocked:
        resp = client.get("/api/render?w=800&h=480&bpp=4&colors=6")
    assert resp.status_code == 200
    assert resp.headers.get("X-Device-Unregistered") == "no-mac"
    mocked.assert_not_awaited()


def test_platform_quota_without_env_key_falls_back_to_account_key():
    """平台 .env 没配 Key + 账号里存着 Key → 用账号的 Key，而不是出废图。"""
    from api.shared import rescue_key_when_platform_unconfigured

    owner_cfg = {"llm_access_mode": "platform", "api_key": "sk-owner-real-key", "provider": "deepseek"}
    with patch("api.shared.platform_key_available", return_value=False):
        cfg, rescue = rescue_key_when_platform_unconfigured(
            {"llm_provider": "deepseek"}, owner_cfg, None, 7, None
        )
    assert cfg["user_api_key"] == "sk-owner-real-key"
    assert rescue is not None and rescue[0] == "owner_api_key" and rescue[1] == 7


def test_platform_key_present_keeps_platform_quota_path():
    """平台 .env 有 Key 时不动用账号 Key（额度照扣，语义不变）。"""
    from api.shared import rescue_key_when_platform_unconfigured

    owner_cfg = {"llm_access_mode": "platform", "api_key": "sk-owner-real-key"}
    with patch("api.shared.platform_key_available", return_value=True):
        cfg, rescue = rescue_key_when_platform_unconfigured(
            {"llm_provider": "aliyun"}, owner_cfg, None, 7, None
        )
    assert "user_api_key" not in cfg
    assert rescue is None


async def test_bound_device_with_byok_renders_without_api_key_error(isolated_db):
    """端到端：设备绑定到配了 Key 的账号 → 正常出图，不再刷「API key 无效」。

    模拟自托管最糟的情况：平台 .env 三个 Key 全是空的
    （``platform_key_available=False``）。只要账号里有 Key，设备就该正常工作。
    """
    from core import db as db_mod
    from core.config_store import create_user

    db = await db_mod.get_main_db()

    db = await db_mod.get_main_db()

    from api.shared import build_image

    user_id = await create_user("byok_user", "pass1234", email="byok@example.com")
    from core.config_store import save_user_llm_config
    from core.stats_store import init_stats_db

    await init_stats_db()
    now = datetime.now().isoformat()
    await db.execute(
        "INSERT INTO device_memberships (mac, user_id, role, status, nickname, created_at, updated_at) "
        "VALUES (?, ?, 'owner', 'active', 'test', ?, ?)",
        (MAC, user_id, now, now),
    )
    await db.commit()
    await save_user_llm_config(
        user_id,
        llm_access_mode="preset",
        provider="deepseek",
        api_key="sk-owner-real-key",
    )

    with patch("api.shared.platform_key_available", return_value=False), \
         patch("core.json_content._call_llm", new=AsyncMock(return_value="今天很好")), \
         patch("core.context.get_weather", new=AsyncMock(return_value={"temp": 20})):
        _img, _persona, _hit, _fallback, _quota, api_key_invalid, _req, usage = (
            await build_image(3.3, MAC, "STOIC", screen_w=800, screen_h=480, colors=6)
        )
    assert api_key_invalid is False, "账号里有 Key 就不该报 API key 无效"
    assert usage == "owner_api_key", usage


def test_masked_key_is_never_rescued():
    """掩码残留（sk-a****1234）不能当 Key 用。"""
    from api.shared import rescue_key_when_platform_unconfigured

    owner_cfg = {"llm_access_mode": "platform", "api_key": "sk-a****1234"}
    with patch("api.shared.platform_key_available", return_value=False):
        cfg, rescue = rescue_key_when_platform_unconfigured({}, owner_cfg, None, 7, None)
    assert "user_api_key" not in cfg
    assert rescue is None
