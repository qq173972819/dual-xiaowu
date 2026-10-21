"""学习模式 · 轻量化推送与老师授权接口。

设计要点（见 学习模式 PRD V1.4 第三、六、七章）：
- 老师**免注册**：家长在 Web 后台生成 6 位授权码，老师凭授权码推送。
- 推送目标：【今日作业】与【老师便签】，支持「覆盖 / 追加」与发送人标记。
- 接口同时服务未来的微信小程序（授权校验仅依赖 auth_code，无需登录）。

本文件只做后端能力；微信小程序端、OCR 识别为独立前端，不在本仓库。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from api.shared import ensure_web_or_device_access
from core.auth import validate_mac_param
from core.config_store import get_main_db, get_dual_config, save_dual_config, set_pending_refresh, normalize_mac

router = APIRouter()


async def _ensure_push_tables() -> None:
    db = await get_main_db()
    await db.execute(
        """CREATE TABLE IF NOT EXISTS teacher_auth (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               device_mac TEXT NOT NULL,
               auth_code TEXT NOT NULL,
               auth_name TEXT,
               permission TEXT DEFAULT 'only_note',
               expire_time TEXT,
               is_frozen INTEGER DEFAULT 0,
               created_at TEXT
           )"""
    )
    await db.execute(
        """CREATE TABLE IF NOT EXISTS push_record (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               device_mac TEXT NOT NULL,
               push_type TEXT,
               sender_type TEXT,
               sender_name TEXT,
               content TEXT,
               is_append INTEGER DEFAULT 1,
               item_count INTEGER DEFAULT 0,
               create_time TEXT
           )"""
    )
    await db.commit()


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


async def _verify_teacher_auth(device_mac: str, auth_code: str) -> Optional[dict]:
    """校验授权码有效（未冻结、未过期）。返回授权行或 None。"""
    device_mac = normalize_mac(device_mac)
    await _ensure_push_tables()
    db = await get_main_db()
    cur = await db.execute(
        "SELECT id, auth_code, auth_name, permission, expire_time, is_frozen "
        "FROM teacher_auth WHERE device_mac=? AND auth_code=?",
        (device_mac, auth_code),
    )
    row = await cur.fetchone()
    if not row:
        return None
    rid, code, name, perm, expire, frozen = row
    if frozen:
        return None
    if expire and expire < _today_str():
        return None
    return {"id": rid, "auth_name": name, "permission": perm or "only_note"}


@router.post("/teacher-auth/generate")
async def generate_teacher_auth(
    request: Request,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Header(default=None, alias="X-Ink-Session"),
):
    """Web 后台：为某设备生成老师授权码（需设备归属权限）。

    body: { device_mac, auth_name, validity_days(7/30/0=永久), permission(only_note/note_and_homework) }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    mac = validate_mac_param(body.get("device_mac") or "")
    await ensure_web_or_device_access(request, mac, x_device_token, ink_session, owner_only=True)
    name = (body.get("auth_name") or "").strip() or "老师"
    days = int(body.get("validity_days") or 30)
    perm = body.get("permission") in ("note_and_homework",) and "note_and_homework" or "only_note"
    expire = None if days <= 0 else (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    await _ensure_push_tables()
    db = await get_main_db()
    # 生成唯一 6 位授权码
    code = None
    for _ in range(20):
        cand = "".join(secrets.choice("0123456789") for _ in range(6))
        cur = await db.execute("SELECT 1 FROM teacher_auth WHERE device_mac=? AND auth_code=?", (mac, cand))
        if not await cur.fetchone():
            code = cand
            break
    if not code:
        return JSONResponse({"error": "code_pool_exhausted"}, status_code=500)
    await db.execute(
        "INSERT INTO teacher_auth (device_mac, auth_code, auth_name, permission, expire_time, is_frozen, created_at) "
        "VALUES (?,?,?,?,?,0,?)",
        (mac, code, name, perm, expire, datetime.now().isoformat()),
    )
    await db.commit()
    return {"ok": True, "auth_code": code, "auth_name": name, "permission": perm,
            "expire_time": expire, "qr_text": f"INKSAUTH:{mac}:{code}"}


@router.get("/teacher-auth/list")
async def list_teacher_auth(
    request: Request,
    mac: str = "",
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Header(default=None, alias="X-Ink-Session"),
):
    """Web 后台：列出某设备的所有授权（含已冻结 / 已过期）。"""
    mac = validate_mac_param(mac)
    await ensure_web_or_device_access(request, mac, x_device_token, ink_session, owner_only=True)
    await _ensure_push_tables()
    db = await get_main_db()
    cur = await db.execute(
        "SELECT id, auth_code, auth_name, permission, expire_time, is_frozen, created_at "
        "FROM teacher_auth WHERE device_mac=? ORDER BY id DESC", (mac,),
    )
    rows = await cur.fetchall()
    today = _today_str()
    return {"items": [
        {"id": r[0], "auth_code": r[1], "auth_name": r[2], "permission": r[3],
         "expire_time": r[4], "is_frozen": bool(r[5]), "created_at": r[6],
         "expired": bool(r[4] and r[4] < today)}
        for r in rows
    ]}


@router.post("/teacher-auth/freeze")
async def freeze_teacher_auth(
    request: Request,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Header(default=None, alias="X-Ink-Session"),
):
    """Web 后台：冻结 / 解冻授权码。body: { device_mac, auth_code, frozen(0/1) }"""
    body = await request.json()
    mac = validate_mac_param(body.get("device_mac") or "")
    await ensure_web_or_device_access(request, mac, x_device_token, ink_session, owner_only=True)
    code = (body.get("auth_code") or "").strip()
    frozen = 1 if body.get("frozen") else 0
    await _ensure_push_tables()
    db = await get_main_db()
    await db.execute(
        "UPDATE teacher_auth SET is_frozen=? WHERE device_mac=? AND auth_code=?",
        (frozen, mac, code),
    )
    await db.commit()
    return {"ok": True, "frozen": bool(frozen)}


@router.post("/push/homework")
async def push_homework(request: Request):
    """老师快捷推送：写入【今日作业】。body: { device_mac, auth_code, content(单行/多行文本), is_append(0/1) }"""
    body = await request.json()
    mac = validate_mac_param(body.get("device_mac") or "")
    code = (body.get("auth_code") or "").strip()
    auth = await _verify_teacher_auth(mac, code)
    if not auth:
        return JSONResponse({"error": "invalid_or_expired_auth"}, status_code=403)
    if auth["permission"] != "note_and_homework":
        return JSONResponse({"error": "permission_denied_for_homework"}, status_code=403)

    raw = (body.get("content") or "").strip()
    if not raw:
        return JSONResponse({"error": "empty_content"}, status_code=400)
    lines = [s.strip() for s in str(raw).replace("\\r", "").split("\\n") if s.strip()]
    if not lines:
        return JSONResponse({"error": "empty_content"}, status_code=400)
    is_append = int(body.get("is_append", 1)) != 0

    cfg = await get_dual_config(mac) or {"version": 1, "sceneKey": "study", "config": {}, "content": {}}
    content = cfg.setdefault("content", {})
    hw = content.get("homework") or []
    if not is_append:
        hw = [h for h in hw if (h.get("date") or "") != _today_str()]  # 清掉当天，覆盖
    for t in lines:
        hw.append({"id": secrets.token_hex(4), "text": t, "done": False,
                   "sender": auth["auth_name"] or "老师", "sender_type": "teacher", "date": _today_str()})
    content["homework"] = hw
    cfg["content"] = content
    await save_dual_config(mac, cfg)
    await set_pending_refresh(mac, True)
    await _log_push(mac, "homework", "teacher", auth["auth_name"], raw, is_append, len(lines))
    return {"ok": True, "appended": len(lines), "mode": "append" if is_append else "overwrite"}


@router.post("/push/note")
async def push_note(request: Request):
    """老师快捷推送：写入【老师便签】。body: { device_mac, auth_code, content, persistent(0/1) }"""
    body = await request.json()
    mac = validate_mac_param(body.get("device_mac") or "")
    code = (body.get("auth_code") or "").strip()
    auth = await _verify_teacher_auth(mac, code)
    if not auth:
        return JSONResponse({"error": "invalid_or_expired_auth"}, status_code=403)

    raw = (body.get("content") or "").strip()
    if not raw:
        return JSONResponse({"error": "empty_content"}, status_code=400)
    persistent = int(body.get("persistent", 0)) != 0
    note = {"id": secrets.token_hex(4), "text": raw, "color": "yellow",
            "sender": auth["auth_name"] or "老师", "sender_type": "teacher", "persistent": persistent, "date": _today_str()}

    cfg = await get_dual_config(mac) or {"version": 1, "sceneKey": "study", "config": {}, "content": {}}
    content = cfg.setdefault("content", {})
    notes = content.get("notes") or []
    notes.append(note)
    content["notes"] = notes
    cfg["content"] = content
    await save_dual_config(mac, cfg)
    await set_pending_refresh(mac, True)
    await _log_push(mac, "note", "teacher", auth["auth_name"], raw, 1, 1)
    return {"ok": True, "persistent": persistent}


@router.get("/push/history")
async def push_history(
    device_mac: str = "",
    auth_code: str = "",
    request: Request = None,
    x_device_token: Optional[str] = Header(default=None),
    ink_session: Optional[str] = Header(default=None, alias="X-Ink-Session"),
):
    """老师 / 后台：查询历史推送记录。老师凭 auth_code+device_mac；后台需 owner 鉴权（前端带 token）。"""
    mac = validate_mac_param(device_mac)
    if auth_code:
        auth = await _verify_teacher_auth(mac, auth_code)
        if not auth:
            return JSONResponse({"error": "invalid_or_expired_auth"}, status_code=403)
    else:
        # 后台查询：必须校验设备归属，否则任何人凭 mac 即可读取推送历史
        await ensure_web_or_device_access(request, mac, x_device_token, ink_session, owner_only=True)
    await _ensure_push_tables()
    db = await get_main_db()
    cur = await db.execute(
        "SELECT id, push_type, sender_type, sender_name, content, is_append, item_count, create_time "
        "FROM push_record WHERE device_mac=? ORDER BY id DESC LIMIT 200", (mac,),
    )
    rows = await cur.fetchall()
    return {"items": [
        {"id": r[0], "push_type": r[1], "sender_type": r[2], "sender_name": r[3],
         "content": r[4], "is_append": bool(r[5]), "item_count": r[6], "create_time": r[7]}
        for r in rows
    ]}


async def _log_push(device_mac: str, ptype: str, sender_type: str, sender_name: str,
                    content: str, is_append: int, count: int) -> None:
    await _ensure_push_tables()
    db = await get_main_db()
    await db.execute(
        "INSERT INTO push_record (device_mac, push_type, sender_type, sender_name, content, is_append, item_count, create_time) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (device_mac, ptype, sender_type, sender_name, content[:2000], is_append, count, datetime.now().isoformat()),
    )
    await db.commit()
