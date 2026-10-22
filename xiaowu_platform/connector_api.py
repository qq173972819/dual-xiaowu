"""连接器 API：绑定页列表、OAuth 授权跳转、回调落库、解绑。

路由分两组：
- /api/v1/mobile/connectors/*  小程序/后台用户侧（Bearer 用户令牌）
- /api/v1/connectors/*         第三方 OAuth 回调 + 开发模式 mock 端点

安全约定：
- state 一次性、10 分钟过期，防 CSRF；
- 凭据经 CredentialVault 加密落库，任何接口不回传明文；
- mock 端点仅在 dev_mode 下注册行为（生产环境返回 404）。
"""
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
import json
import os
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse

from .connectors import ConnectorRegistry, CredentialVault
from .settings import settings
from .store import Store, now_iso

store = Store(settings.database_path)
router = APIRouter()


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


def _base_url() -> str:
    return settings.public_base_url.rstrip("/")


def _exchange_token(connector: dict[str, Any], code: str) -> dict[str, Any]:
    """用授权码换取第三方 token（标准 OAuth2 authorization_code）。"""
    auth = connector.get("auth") or {}
    token_url = auth.get("token_url") or ""
    if not token_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "连接器未配置 token_url")
    if token_url.startswith("/"):
        token_url = _base_url() + token_url
    client_secret = os.getenv(auth["client_secret_env"], "") if auth.get("client_secret_env") else auth.get("client_secret", "")
    form = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{_base_url()}/api/v1/connectors/{connector['id']}/callback",
        "client_id": auth.get("client_id", ""),
        "client_secret": client_secret or "",
    }).encode()
    req = urllib.request.Request(token_url, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return dict(json.loads(resp.read().decode()))
    except Exception as exc:  # noqa: BLE001 —— 网络失败统一转 502
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"token 交换失败: {exc}") from exc


# ——— 用户侧：绑定页列表 ———
@router.get("/api/v1/mobile/connectors")
def list_connectors(user_id: str = Depends(current_user)) -> list[dict[str, Any]]:
    """绑定页数据源：platform → 无需绑定已开通；user → 已绑定/去授权。"""
    from .main import connector_registry
    bindings = {b["connector_id"]: b for b in store.list_connector_bindings(user_id)}
    items: list[dict[str, Any]] = []
    for manifest in connector_registry.load().values():
        pub = connector_registry.public_dict(manifest)
        if pub["type"] == "platform":
            pub.update({"status": "included", "status_label": "无需绑定，已开通", "bind_url": ""})
        else:
            bound = bindings.get(manifest["id"])
            if bound:
                pub.update({
                    "status": "bound",
                    "status_label": "已绑定",
                    "external_account": bound["external_account"],
                    "updated_at": bound["updated_at"],
                })
            else:
                pub.update({
                    "status": "unbound",
                    "status_label": "去授权",
                    "bind_url": f"/api/v1/mobile/connectors/{manifest['id']}/authorize",
                })
        items.append(pub)
    return items


# ——— 用户侧：发起授权（返回第三方授权页地址，页面引导用户打开）———
@router.post("/api/v1/mobile/connectors/{connector_id}/authorize")
def authorize(connector_id: str, user_id: str = Depends(current_user)) -> dict[str, str]:
    from .main import connector_registry
    connector = connector_registry.get(connector_id)
    if not connector:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "连接器不存在")
    if connector.get("type") != "user":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "平台级连接器无需绑定")
    auth = connector.get("auth") or {}
    state = store.create_connector_state(user_id, connector_id)
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": auth.get("client_id", ""),
        "redirect_uri": f"{_base_url()}/api/v1/connectors/{connector_id}/callback",
        "scope": " ".join(auth.get("scopes", [])),
        "state": state,
    })
    authorize_url = (auth.get("authorize_url") or "") + ("&" if "?" in auth.get("authorize_url", "") else "?") + params
    if authorize_url.startswith("/"):
        authorize_url = _base_url() + authorize_url
    store.audit(user_id, "connector.authorize", connector_id, {"state": state})
    return {"authorize_url": authorize_url, "state": state}


# ——— 第三方回调：换 token → 加密入库 ———
@router.get("/api/v1/connectors/{connector_id}/callback", response_class=HTMLResponse)
def oauth_callback(connector_id: str, code: str = "", state: str = "") -> str:
    from .main import connector_registry, credential_vault
    connector = connector_registry.get(connector_id)
    if not connector:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "连接器不存在")
    consumed = store.consume_connector_state(state or "-")
    if not consumed or consumed["connector_id"] != connector_id:
        return _result_page(False, "授权状态无效或已过期，请回到绑定页重新点击「去授权」。")
    token_payload = _exchange_token(connector, code)
    expires_in = token_payload.get("expires_in")
    expires_at = (datetime.now(UTC) + timedelta(seconds=int(expires_in))).isoformat() if expires_in else None
    external = token_payload.get("external_account") or token_payload.get("openid") or token_payload.get("sub") or ""
    store.save_connector_binding(
        consumed["user_id"], connector_id,
        credential_vault.encrypt(token_payload), str(external),
        (connector.get("auth") or {}).get("scopes", []), expires_at,
    )
    store.audit(consumed["user_id"], "connector.bind", connector_id, {"external_account": external})
    return _result_page(True, f"「{connector.get('name', connector_id)}」绑定成功，可以返回小程序查看。")


def _result_page(ok: bool, message: str) -> str:
    color = "#0f6e56" if ok else "#a32d2d"
    return (f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>连接器绑定结果</title></head>"
            f"<body style=\"font-family:system-ui,sans-serif;max-width:480px;margin:64px auto;padding:0 20px\">"
            f"<h2 style='color:{color}'>{'绑定成功' if ok else '绑定失败'}</h2>"
            f"<p>{message}</p></body></html>")


# ——— 用户侧：解绑 ———
@router.post("/api/v1/mobile/connectors/{connector_id}/unbind")
def unbind(connector_id: str, user_id: str = Depends(current_user)) -> dict[str, str]:
    from .main import connector_registry
    if not connector_registry.get(connector_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "连接器不存在")
    if not store.delete_connector_binding(user_id, connector_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "尚未绑定该连接器")
    store.audit(user_id, "connector.unbind", connector_id, {})
    return {"status": "unbound"}


# ——— 开发模式：demo-oauth 的本地 mock 授权服务器（离线可跑通全流程）———
@router.get("/api/v1/connectors/{connector_id}/mock-authorize")
def mock_authorize(connector_id: str, state: str = "") -> Any:
    if not settings.dev_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    redirect = f"{_base_url()}/api/v1/connectors/{connector_id}/callback?code=mock-code-{state[:8]}&state={urllib.parse.quote(state)}"
    return RedirectResponse(redirect, status_code=302)


@router.post("/api/v1/connectors/{connector_id}/mock-token")
def mock_token(connector_id: str) -> dict[str, Any]:
    if not settings.dev_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"access_token": f"mock-at-{connector_id}-{now_iso()}", "token_type": "bearer",
            "expires_in": 86400, "external_account": f"demo-user@{connector_id}"}
