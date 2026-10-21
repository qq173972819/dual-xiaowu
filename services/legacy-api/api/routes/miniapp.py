"""微信小程序专用接口层。

补齐小程序 PRD 需要、但后端原先缺失的接口。实现上**复用既有能力**
（设备归属鉴权 / pending_refresh / teacher_auth / dual_config），
与 dual 控制台共用同一份数据，保证 Web 后台与小程序看到的内容完全一致。

- POST /api/auth/wechat          微信 code → openid → 会话 token（首次自动建号并绑定 openid）
- POST /api/auth/verify          老师授权码校验（老师通道免注册）
- POST /api/device/{mac}/sync    A 屏立即同步（5 分钟限流，保护墨水屏）
- GET  /api/device/snapshot      双屏快照预览地址
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Cookie, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.shared import ensure_web_or_device_access
from core.auth import create_session_token, validate_mac_param
from core.config_store import (
    create_user,
    get_dual_config,
    get_device_state,
    get_main_db,
    set_pending_refresh,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["miniapp"])

# 同步限流（进程内）：与 PRD「5 分钟限流」一致，保护墨水屏刷新寿命
_SYNC_COOLDOWN_S = 300
_sync_last: dict[str, float] = {}

_WECHAT_SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


async def _ensure_wechat_table(db) -> None:
    await db.execute(
        """CREATE TABLE IF NOT EXISTS wechat_openid_map (
               openid TEXT PRIMARY KEY,
               user_id INTEGER NOT NULL,
               created_at TEXT
           )"""
    )
    await db.commit()


async def _ensure_teacher_table(db) -> None:
    """与 push.py 中 teacher_auth 表结构保持一致（幂等，字段缺失不影响已有表）。"""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS teacher_auth (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               device_mac TEXT NOT NULL,
               auth_code TEXT NOT NULL,
               auth_name TEXT DEFAULT '',
               permission TEXT DEFAULT 'only_note',
               expire_time TEXT,
               is_frozen INTEGER DEFAULT 0,
               created_at TEXT
           )"""
    )
    await db.commit()


class WechatLoginIn(BaseModel):
    code: str = ""


@router.post("/auth/wechat")
async def wechat_login(body: WechatLoginIn):
    """家长微信一键登录：wx.login 拿到 code，换取 openid 并签发会话 token。

    首次登录自动建号（用户名 wx_<openid 尾 12 位>，随机密码）并写入 openid 绑定表，
    后续同一微信再次登录直接复用该账号 —— 设备绑定、双屏配置因此不会丢失。
    """
    appid = (os.getenv("WECHAT_MINIAPP_APPID") or os.getenv("WECHAT_APPID") or "").strip()
    secret = (os.getenv("WECHAT_MINIAPP_SECRET") or os.getenv("WECHAT_APPSECRET") or "").strip()
    code = (body.code or "").strip()

    if not code:
        return JSONResponse({"error": "缺少微信登录 code"}, status_code=400)
    if not appid or not secret:
        return JSONResponse(
            {"error": "后端未配置小程序凭据（WECHAT_MINIAPP_APPID / WECHAT_MINIAPP_SECRET）"},
            status_code=503,
        )

    params = {
        "appid": appid,
        "secret": secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_WECHAT_SESSION_URL, params=params)
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 - 网络异常统一降级为 502
        logger.warning("[MINIAPP] jscode2session failed: %s", exc)
        return JSONResponse({"error": f"微信登录失败：{exc}"}, status_code=502)

    if data.get("errcode"):
        return JSONResponse(
            {"error": f"微信登录失败：{data.get('errmsg', '未知错误')}（errcode={data.get('errcode')}）"},
            status_code=401,
        )

    openid = (data.get("openid") or "").strip()
    if not openid:
        return JSONResponse({"error": "微信未返回 openid"}, status_code=401)

    db = await get_main_db()
    await _ensure_wechat_table(db)

    cursor = await db.execute("SELECT user_id FROM wechat_openid_map WHERE openid=?", (openid,))
    row = await cursor.fetchone()
    is_new = False

    if row:
        user_id = row[0]
    else:
        username = "wx_" + openid[-12:]
        user_id = await create_user(username, secrets.token_urlsafe(16))
        if not user_id:
            return JSONResponse({"error": "创建微信账号失败"}, status_code=500)
        await db.execute(
            "INSERT OR REPLACE INTO wechat_openid_map (openid, user_id, created_at) VALUES (?,?,?)",
            (openid, user_id, datetime.now().isoformat()),
        )
        await db.commit()
        is_new = True

    cursor = await db.execute("SELECT username FROM users WHERE id=?", (user_id,))
    urow = await cursor.fetchone()
    username = urow[0] if urow else ("wx_" + openid[-12:])

    token = create_session_token(user_id, username)
    logger.info("[MINIAPP] wechat login ok user_id=%s is_new=%s", user_id, is_new)
    return {
        "ok": True,
        "token": token,
        "user_id": user_id,
        "username": username,
        "openid": openid,
        "is_new": is_new,
    }


@router.post("/auth/verify")
async def verify_teacher_auth(body: dict):
    """老师通道：仅凭 6 位授权码校验身份（免注册）。

    小程序侧不知道设备 MAC，因此这里按 auth_code 单字段反查。
    返回设备与学生信息，供小程序锁定「仅该设备、仅追加作业」的权限边界。
    """
    code = str(body.get("authCode") or body.get("auth_code") or "").strip()
    if not code:
        return JSONResponse({"error": "缺少授权码"}, status_code=400)

    db = await get_main_db()
    await _ensure_teacher_table(db)
    cursor = await db.execute(
        "SELECT id, device_mac, auth_name, permission, expire_time, is_frozen "
        "FROM teacher_auth WHERE auth_code=?",
        (code,),
    )
    row = await cursor.fetchone()
    if not row:
        return JSONResponse({"error": "授权码无效"}, status_code=404)

    _rid, mac, name, perm, expire, frozen = row
    if frozen:
        return JSONResponse({"error": "该授权码已被冻结"}, status_code=403)
    if expire and expire < datetime.now().strftime("%Y-%m-%d"):
        return JSONResponse({"error": "该授权码已过期"}, status_code=403)

    state = await get_device_state(mac) or {}
    return {
        "ok": True,
        "authCode": code,
        "authName": name or "老师",
        "deviceMac": mac,
        "deviceSn": mac,
        "studentName": state.get("nickname") or "学生",
        "permission": perm or "only_note",
        "expireTime": expire,
    }


@router.post("/device/{mac}/sync")
async def sync_device(
    mac: str,
    request: Request,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    """A 屏立即同步：置 pending_refresh，设备下次唤醒即刷新。

    带 5 分钟限流（进程内），返回 cooldown 标志供小程序提示。
    """
    mac_norm = validate_mac_param(mac)
    await ensure_web_or_device_access(request, mac_norm, x_device_token, ink_session)

    now = time.time()
    last = _sync_last.get(mac_norm, 0)
    if now - last < _SYNC_COOLDOWN_S:
        wait = int(_SYNC_COOLDOWN_S - (now - last))
        return {"ok": False, "cooldown": True, "message": f"墨水屏保护中，请 {wait} 秒后再同步"}

    _sync_last[mac_norm] = now
    await set_pending_refresh(mac_norm, True)
    logger.info("[MINIAPP] sync requested for %s", mac_norm)
    return {"ok": True, "cooldown": False, "message": "已推送，设备下次唤醒即刷新"}


@router.get("/device/snapshot")
async def device_snapshot(
    request: Request,
    mac: str = Query(...),
    screen: str = Query(default="a"),
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Cookie(default=None),
):
    """双屏快照预览地址（A/B 屏各一张）。

    返回可直接请求的 /api/preview 地址（携带 mac 与面板色彩参数），
    小程序用 wx.downloadFile 带 Authorization 下载后展示。
    """
    mac_norm = validate_mac_param(mac)
    await ensure_web_or_device_access(request, mac_norm, x_device_token, ink_session)

    cfg = await get_dual_config(mac_norm) or {}
    screen_spec = cfg.get("screen") or {}
    colors = int(screen_spec.get("colors") or 4)
    scene_key = cfg.get("sceneKey") or "family"
    scene_cfg = (cfg.get("config") or {}).get(scene_key) or {}

    if screen.lower() == "a":
        persona = (scene_cfg.get("a") or {}).get("mode") or "STOIC"
        url = f"/api/preview?mac={mac_norm}&persona={persona}&colors=2&bpp=1"
    else:
        url = f"/api/preview?mac={mac_norm}&persona=STOIC&colors={colors}&bpp=2"

    return {"ok": True, "url": url, "mac": mac_norm, "screen": screen.lower()}
