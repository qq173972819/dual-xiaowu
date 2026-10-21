"""用户内容后台（dual 控制台）全部接口。

前端（apps/content-console 即原 dual 后台）通过 /backend 反代调用这里的 /api/* 端点，
复用既有 users / device_bindings / content_configs 体系，新增少量表支撑共享成员、
老师授权、个人大模型配置、孩子信息、服务设置与图片上传。
"""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
import json
import os
import secrets
import time
import urllib.request
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .settings import settings
from .store import Store, now_iso

store = Store(settings.database_path)
router = APIRouter()

ONLINE_WINDOW = timedelta(minutes=15)


def current_user(
    x_user_token: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    token = x_user_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    user_id = store.session_user(token)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效，请重新登录")
    return user_id


def optional_user(
    x_user_token: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    token = x_user_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    return store.session_user(token)


def online(last_seen: str | None) -> bool:
    if not last_seen:
        return False
    try:
        return datetime.fromisoformat(last_seen.replace("Z", "+00:00")) > datetime.now(UTC) - ONLINE_WINDOW
    except ValueError:
        return False


# ——— 认证 ———
class RegisterIn(BaseModel):
    username: str
    password: str
    email: str = ""
    phone: str = ""
    phone_region: str = "CN"


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/api/auth/register")
def auth_register(body: RegisterIn) -> dict[str, Any]:
    try:
        store.register_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True, "message": "注册成功"}


@router.post("/api/auth/login")
def auth_login(body: LoginIn) -> dict[str, Any]:
    result = store.login(body.username, body.password)
    if not result:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    user_id, token = result
    return {"user_id": user_id, "token": token}


@router.get("/api/auth/me")
def auth_me(user_id: str = Depends(current_user)) -> dict[str, Any]:
    with store.connect() as db:
        row = db.execute("SELECT username FROM users WHERE user_id=?", (user_id,)).fetchone()
    return {"user_id": user_id, "username": row["username"] if row else user_id}


@router.post("/api/auth/reset-password/send-code")
def reset_send(body: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "message": "验证码已发送（演示环境未真正发信）"}


@router.post("/api/auth/reset-password")
def reset_do(body: dict[str, Any]) -> dict[str, Any]:
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or len(password) < 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "参数不完整")
    with store.connect() as db:
        row = db.execute("SELECT user_id FROM users WHERE username=?", (email,)).fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
        import secrets
        salt = secrets.token_hex(16)
        db.execute("UPDATE users SET password_hash=?, password_salt=? WHERE user_id=?",
                   (store._password_hash(password, salt), salt, row["user_id"]))
    return {"ok": True, "message": "密码已重置"}


# ——— 设备 ———
@router.get("/api/user/devices")
def user_devices(user_id: str = Depends(current_user)) -> dict[str, Any]:
    rows = store.user_devices(user_id)
    devices = [
        {
            "mac": r["device_id"],
            "nickname": r["nickname"],
            "role": "owner",
            "status": "online" if online(r["last_seen_at"]) else "offline",
            "is_online": online(r["last_seen_at"]),
            "last_seen": r["last_seen_at"] or "暂无记录",
            "model": r.get("model"),
        }
        for r in rows
    ]
    return {"devices": devices}


class BindIn(BaseModel):
    mac: str
    nickname: str = ""


@router.post("/api/user/devices")
def user_bind(body: BindIn, user_id: str = Depends(current_user)) -> dict[str, Any]:
    mac = body.mac.strip().upper()
    try:
        store.bind_device(user_id, mac, body.nickname or "我的小悟双屏")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True, "mac": mac}


@router.delete("/api/user/devices/{mac}")
def user_unbind(mac: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    store.unbind_device(user_id, mac.strip().upper())
    return {"ok": True}


class ShareIn(BaseModel):
    username: str


@router.post("/api/user/devices/{mac}/share")
def user_share(mac: str, body: ShareIn, user_id: str = Depends(current_user)) -> dict[str, Any]:
    try:
        store.share_device(user_id, mac.strip().upper(), body.username.strip(), "member")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True}


@router.get("/api/user/devices/{mac}/members")
def user_members(mac: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    rows = store.device_members(mac.strip().upper())
    members = [
        {"user_id": r["user_id"], "username": r["username"], "role": "owner"}
        for r in rows
    ]
    return {"members": members}


@router.delete("/api/user/devices/{mac}/members/{target_user_id}")
def user_remove_member(mac: str, target_user_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not store.user_owns(user_id, mac):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "你不是该设备的所有者")
    if target_user_id == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能移除设备所有者本人")
    store.remove_member(mac, target_user_id.strip())
    return {"ok": True}


class ClaimIn(BaseModel):
    pair_code: str


@router.post("/api/claim/consume")
def claim(body: ClaimIn, user_id: str = Depends(current_user)) -> dict[str, Any]:
    try:
        device_id = store.consume_claim(body.pair_code, user_id, "我的小悟双屏")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True, "mac": device_id}


# ——— 双屏内容配置 ———
@router.get("/api/device/{mac}/dual-config")
def get_dual_config(
    mac: str,
    x_device_token: Annotated[str | None, Header()] = None,
    user_id: str | None = Depends(optional_user),
) -> dict[str, Any]:
    mac = mac.strip().upper()
    # 固件 LoadDualConfig 用 X-Device-Token；Web/小程序用用户归属
    if x_device_token and store.verify_device_token(mac, x_device_token):
        pass
    elif user_id and store.user_owns(user_id, mac):
        pass
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问该设备配置")
    cfg = store.get_content_config(mac)
    if not cfg:
        return {"config": None, "updated_at": ""}
    return cfg


class DualConfigIn(BaseModel):
    payload: dict[str, Any]


@router.post("/api/device/{mac}/dual-config")
def post_dual_config(mac: str, body: DualConfigIn, user_id: str = Depends(current_user)) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not store.user_owns(user_id, mac):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "设备未绑定到当前账号")
    ts = store.save_content_config(mac, body.payload)
    store.audit(user_id, "dual-config.save", mac, {"keys": list(body.payload.keys())})
    return {"ok": True, "updated_at": ts}


# ——— 天气预览（演示数据，方便配置界面查看效果）———
@router.get("/api/device/{mac}/dual-weather")
def dual_weather(mac: str, city: str = "", user_id: str = Depends(current_user)) -> dict[str, Any]:
    return {
        "city": city or "杭州",
        "today_temp": "22°",
        "current_temp": "21°",
        "today_desc": "多云转晴",
        "today_code": 2,
        "today_low": "16°",
        "today_high": "25°",
        "today_humidity": "62%",
        "today_wind_dir": "东南风",
        "today_wind_level": "3 级",
        "sunrise": "06:12",
        "sunset": "17:48",
        "advice": "适宜出行，早晚温差较大，注意添衣。",
        "forecast": [
            {"day": "明天", "date": "10-21", "temp_range": "17°~26°", "desc": "晴", "code": 0},
            {"day": "后天", "date": "10-22", "temp_range": "18°~27°", "desc": "多云", "code": 1},
            {"day": "周四", "date": "10-23", "temp_range": "16°~24°", "desc": "小雨", "code": 61},
        ],
    }


# ——— 老师授权 ———
class TeacherGenIn(BaseModel):
    device_mac: str
    auth_name: str = "老师"
    validity_days: int = 30
    permission: str = "only_note"


@router.post("/api/teacher-auth/generate")
def teacher_generate(body: TeacherGenIn, user_id: str = Depends(current_user)) -> dict[str, Any]:
    mac = body.device_mac.strip().upper()
    if not store.user_owns(user_id, mac):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "设备未绑定到当前账号")
    row = store.create_teacher_code(mac, body.auth_name or "老师", body.permission, user_id, body.validity_days)
    return {"auth_code": row["auth_code"], "expire_time": row["expire_time"], "qr_text": f"XIAOWU-TEACHER:{row['auth_code']}"}


@router.get("/api/teacher-auth/list")
def teacher_list(mac: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not store.user_owns(user_id, mac):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "设备未绑定到当前账号")
    return {"items": store.list_teacher_codes(mac)}


class TeacherFreezeIn(BaseModel):
    device_mac: str
    auth_code: str
    frozen: int = 1


@router.post("/api/teacher-auth/freeze")
def teacher_freeze(body: TeacherFreezeIn, user_id: str = Depends(current_user)) -> dict[str, Any]:
    mac = body.device_mac.strip().upper()
    if not store.user_owns(user_id, mac):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "设备未绑定到当前账号")
    store.freeze_teacher_code(mac, body.auth_code.strip().upper(), body.frozen == 1)
    return {"ok": True}


# ——— 个人大模型配置 / 孩子信息 ———
@router.get("/api/user/profile")
def user_profile(user_id: str = Depends(current_user)) -> dict[str, Any]:
    return {"llm_config": store.get_user_profile(user_id, "llm") or {}}


@router.put("/api/user/profile/llm")
def put_llm(body: dict[str, Any], user_id: str = Depends(current_user)) -> dict[str, Any]:
    store.set_user_profile(user_id, "llm", body)
    return {"ok": True}


@router.get("/api/user/child")
def get_child(user_id: str = Depends(current_user)) -> dict[str, Any]:
    return {"child": store.get_user_profile(user_id, "child") or {}}


@router.put("/api/user/child")
def put_child(body: dict[str, Any], user_id: str = Depends(current_user)) -> dict[str, Any]:
    store.set_user_profile(user_id, "child", body)
    return {"ok": True}


# ——— 系统配置（服务设置，如快递100）———
@router.get("/api/settings/service")
def get_service() -> dict[str, Any]:
    return store.get_service_config()


class ServiceIn(BaseModel):
    key: str
    value: Any


@router.post("/api/settings/service")
def post_service(body: ServiceIn) -> dict[str, Any]:
    cfg = store.get_service_config()
    cfg[body.key] = body.value
    store.set_service_config(cfg)
    return {"ok": True}


# ——— 图片上传（相册）———
UPLOAD_DIR = settings.database_path.parent / "uploads"
ALLOWED = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@router.post("/api/uploads")
def upload(file: UploadFile = File(...), user_id: str = Depends(current_user)) -> dict[str, Any]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持图片文件")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    import secrets
    fname = f"{secrets.token_hex(12)}{ext}"
    data = file.file.read()
    (UPLOAD_DIR / fname).write_bytes(data)
    return {"id": fname, "url": f"/uploads/{fname}"}


# ——— 快递追踪（演示实现，可在系统配置填入快递100 凭据后切换真实接口）———
@router.post("/api/express/track")
def express_track(body: dict[str, Any] = {}) -> dict[str, Any]:
    tracking = (body.get("trackingNumber") or body.get("tracking") or "").strip()
    if not tracking:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少快递单号")
    cfg = store.get_service_config()
    if cfg.get("kuaidi100_key") and cfg.get("kuaidi100_customer"):
        # 真实接口需后端联调，这里返回结构占位，避免前端报错
        return {"ok": True, "trackingNumber": tracking, "carrier": "未知",
                "status": "已签收", "traces": [], "demo": True}
    # 演示数据
    return {
        "ok": True,
        "trackingNumber": tracking,
        "carrier": "顺丰速运",
        "status": "运输中",
        "demo": True,
        "traces": [
            {"time": "2026-10-20 09:12", "desc": "【成都】快件已发往 极客谷体验中心"},
            {"time": "2026-10-19 21:40", "desc": "【深圳】快件已到达 深圳转运中心"},
            {"time": "2026-10-19 14:05", "desc": "【深圳】顺丰已收取快件"},
        ],
    }


# ——— 作业 OCR（演示实现：接收图片，返回可编辑文本）———
@router.post("/api/ocr/homework")
async def ocr_homework(file: UploadFile = File(None), body: dict[str, Any] = {}) -> dict[str, Any]:
    # 无外部视觉模型时返回空文本，前端进入手动编辑模式，不阻断流程
    return {"ok": True, "text": "", "lines": [], "demo": True,
            "message": "演示环境未接入 OCR，请手动输入作业内容"}


# ——— 设备令牌（固件启动领取，供 X-Device-Token 鉴权）———
@router.post("/api/device/{mac}/token")
def device_token(mac: str, body: dict[str, Any] = {}) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not store.device_exists(mac):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "设备未注册")
    token = store.mint_device_token(mac)
    return {"token": token, "device_id": mac}


# ——— 设备解绑自身（固件 UnbindSelf）———
@router.post("/api/device/{mac}/unbind")
def device_unbind(mac: str, x_device_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not (x_device_token and store.verify_device_token(mac, x_device_token)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "设备令牌无效")
    store.unbind_device_self(mac)
    return {"ok": True, "message": "设备已解绑"}


# ——— 设备上报运行模式（固件 ReportRuntimeMode）———
@router.post("/api/device/{mac}/runtime")
def device_runtime(mac: str, body: dict[str, Any] = {}, x_device_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not (x_device_token and store.verify_device_token(mac, x_device_token)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "设备令牌无效")
    mode = (body.get("mode") or "interval")
    awake = bool(body.get("awake"))
    store.report_runtime(mac, mode, awake)
    return {"ok": True}


# ——— 微信小程序：家长一键登录 ———
_WEXIN_JSCODE_URL = "https://api.weixin.qq.com/sns/jscode2session"


@router.post("/api/auth/wechat")
def wechat_login(body: dict[str, Any] = {}) -> dict[str, Any]:
    appid = (os.getenv("WECHAT_MINIAPP_APPID") or os.getenv("WECHAT_APPID") or "").strip()
    secret = (os.getenv("WECHAT_MINIAPP_SECRET") or os.getenv("WECHAT_APPSECRET") or "").strip()
    code = (body.get("code") or "").strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少微信登录 code")
    if not appid or not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "后端未配置小程序凭据（WECHAT_MINIAPP_APPID/SECRET）")
    try:
        url = _WEXIN_JSCODE_URL + "?" + urllib.parse.urlencode(
            {"appid": appid, "secret": secret, "js_code": code, "grant_type": "authorization_code"})
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"微信登录失败：{exc}")
    if data.get("errcode"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"微信登录失败：{data.get('errmsg','')}")
    openid = (data.get("openid") or "").strip()
    if not openid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "微信未返回 openid")
    uid, username, is_new = store.get_or_create_wechat_user(openid)
    token = store.create_session_token(uid)
    return {"ok": True, "token": token, "user_id": uid, "username": username,
            "openid": openid, "is_new": is_new}


# ——— 微信小程序：老师授权码校验（免注册）———
@router.post("/api/auth/verify")
def verify_teacher_auth(body: dict[str, Any] = {}) -> dict[str, Any]:
    code = str(body.get("authCode") or body.get("auth_code") or "").strip().upper()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少授权码")
    # 授权码可能跨设备，这里全局查
    with store.connect() as db:
        row = db.execute(
            "SELECT device_id, auth_name, permission, expire_at, frozen FROM teacher_codes WHERE auth_code=?",
            (code,)).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "授权码无效")
    device_id, auth_name, perm, expire_at, frozen = row["device_id"], row["auth_name"], row["permission"], row["expire_at"], row["frozen"]
    if frozen:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该授权码已被冻结")
    if expire_at and expire_at < now_iso():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该授权码已过期")
    cfg = store.get_content_config(device_id) or {}
    student = (cfg.get("content", {}).get("child") or {}).get("name") or "学生"
    return {"ok": True, "authCode": code, "authName": auth_name or "老师", "deviceMac": device_id,
            "deviceSn": device_id, "studentName": student, "permission": perm or "only_note", "expireTime": expire_at}


# ——— 家长设备列表（小程序 /api/device/list）———
@router.get("/api/device/list")
def device_list(user_id: str = Depends(current_user)) -> dict[str, Any]:
    items = store.user_devices(user_id)
    out = []
    for d in items:
        out.append({
            "mac": d["device_id"], "sn": d["device_id"], "name": d.get("nickname") or "小悟看板",
            "nickname": d.get("nickname") or "", "is_online": online(d.get("last_seen_at")),
            "last_seen": d.get("last_seen_at") or "",
        })
    return {"devices": out}


# ——— 老师快捷推送：今日作业 / 老师便签 ———
_SYNC_COOLDOWN = 300
_sync_last: dict[str, float] = {}


def _verify_teacher(mac: str, code: str) -> dict[str, Any] | None:
    rec = store.get_teacher_code(mac, code)
    if not rec:
        return None
    if rec["is_frozen"]:
        return None
    if rec["expired"]:
        return None
    return rec


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@router.post("/api/push/homework")
def push_homework(body: dict[str, Any] = {}) -> dict[str, Any]:
    mac = str(body.get("device_mac") or body.get("mac") or "").strip().upper()
    code = str(body.get("auth_code") or "").strip()
    auth = _verify_teacher(mac, code)
    if not auth:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "授权码无效或已失效")
    if auth["permission"] != "note_and_homework":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该授权码无权推送作业")
    raw = (body.get("content") or "").strip()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "作业内容为空")
    lines = [s.strip() for s in str(raw).replace("\r", "").split("\n") if s.strip()]
    is_append = int(body.get("is_append", 1)) != 0
    cfg = store.get_content_config(mac) or {"version": 1, "sceneKey": "study", "config": {}, "content": {}}
    content = cfg.setdefault("content", {})
    hw = content.get("homework") or []
    if not is_append:
        hw = [h for h in hw if (h.get("date") or "") != _today()]
    for t in lines:
        hw.append({"id": secrets.token_hex(4), "text": t, "done": False,
                   "sender": auth["auth_name"] or "老师", "sender_type": "teacher", "date": _today()})
    content["homework"] = hw
    cfg["content"] = content
    store.save_content_config(mac, cfg)
    store.set_pending_refresh(mac, True)
    store.save_push(mac, "homework", "teacher", auth["auth_name"] or "老师", raw, is_append, len(lines))
    return {"ok": True, "appended": len(lines), "mode": "append" if is_append else "overwrite"}


@router.post("/api/push/note")
def push_note(body: dict[str, Any] = {}) -> dict[str, Any]:
    mac = str(body.get("device_mac") or body.get("mac") or "").strip().upper()
    code = str(body.get("auth_code") or "").strip()
    auth = _verify_teacher(mac, code)
    if not auth:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "授权码无效或已失效")
    raw = (body.get("content") or "").strip()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "便签内容为空")
    persistent = int(body.get("persistent", 0)) != 0
    note = {"id": secrets.token_hex(4), "text": raw, "color": "yellow",
            "sender": auth["auth_name"] or "老师", "sender_type": "teacher", "persistent": persistent, "date": _today()}
    cfg = store.get_content_config(mac) or {"version": 1, "sceneKey": "study", "config": {}, "content": {}}
    content = cfg.setdefault("content", {})
    notes = content.get("notes") or []
    notes.append(note)
    content["notes"] = notes
    cfg["content"] = content
    store.save_content_config(mac, cfg)
    store.set_pending_refresh(mac, True)
    store.save_push(mac, "note", "teacher", auth["auth_name"] or "老师", raw, 1, 1)
    return {"ok": True, "persistent": persistent}


@router.get("/api/push/history")
def push_history(device_mac: str = "", auth_code: str = "", user_id: str | None = Depends(optional_user)) -> dict[str, Any]:
    mac = device_mac.strip().upper()
    if auth_code:
        if not _verify_teacher(mac, auth_code):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "授权码无效或已失效")
    elif user_id and store.user_owns(user_id, mac):
        pass
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该设备推送历史")
    items = store.get_push_history(mac)
    return {"items": items}


@router.delete("/api/push/history/{push_id}")
def delete_push(device_mac: str, push_id: int, user_id: str = Depends(current_user)) -> dict[str, Any]:
    mac = device_mac.strip().upper()
    if not store.user_owns(user_id, mac):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权操作该设备")
    ok = store.delete_push(mac, push_id)
    return {"ok": ok}


# ——— A 屏立即同步（5 分钟限流，保护墨水屏）———
@router.post("/api/device/{mac}/sync")
def sync_device(mac: str, user_id: str | None = Depends(optional_user),
                x_device_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not (x_device_token and store.verify_device_token(mac, x_device_token)) and not (user_id and store.user_owns(user_id, mac)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权同步该设备")
    now = time.time()
    last = _sync_last.get(mac, 0)
    if now - last < _SYNC_COOLDOWN:
        wait = int(_SYNC_COOLDOWN - (now - last))
        return {"ok": False, "cooldown": True, "message": f"墨水屏保护中，请 {wait} 秒后再同步"}
    _sync_last[mac] = now
    store.set_pending_refresh(mac, True)
    return {"ok": True, "cooldown": False, "message": "已推送，设备下次唤醒即刷新"}


# ——— 双屏快照预览地址（小程序 /api/device/snapshot）———
@router.get("/api/device/snapshot")
def device_snapshot(mac: str = Query(...), screen: str = Query(default="a"),
                   user_id: str | None = Depends(optional_user),
                   x_device_token: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    mac = mac.strip().upper()
    if not (x_device_token and store.verify_device_token(mac, x_device_token)) and not (user_id and store.user_owns(user_id, mac)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该设备")
    cfg = store.get_content_config(mac) or {}
    screen_spec = cfg.get("screen") or {}
    colors = int(screen_spec.get("colors") or 4)
    scene_key = cfg.get("sceneKey") or "family"
    scene_cfg = (cfg.get("config") or {}).get(scene_key) or {}
    if screen.lower() == "a":
        persona = (scene_cfg.get("a") or {}).get("mode") or "STOIC"
        url = f"/api/preview?mac={mac}&persona={persona}&colors=2&bpp=1"
    else:
        url = f"/api/preview?mac={mac}&persona=STOIC&colors={colors}&bpp=2"
    return {"ok": True, "url": url, "mac": mac, "screen": screen.lower()}
