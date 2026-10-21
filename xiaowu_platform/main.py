from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import HTMLResponse

from .models import ContentConfigIn, ContentPush, DeveloperAppCreate, DeviceCapabilities, DeviceEvent, DeviceRegister, Heartbeat, MobileContentPush, PasswordLogin, TaskAck, TemplateUpsert, WebhookCreate
from .plugin_runtime import PluginRegistry
from .settings import settings
from .store import Store
from .console_api import router as console_router
from .render_api import router as render_router

store = Store(settings.database_path)
plugin_registry = PluginRegistry(Path(__file__).resolve().parents[1] / "plugins")


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()
    yield


app = FastAPI(title="XiaoWu S3 Open Platform", version="0.1.0", lifespan=lifespan)

app.include_router(console_router)
app.include_router(render_router)


def current_app(authorization: Annotated[str | None, Header()] = None,
                x_app_key: Annotated[str | None, Header()] = None) -> dict:
    token = x_app_key or ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    app_info = store.authenticate_app(token)
    if not app_info:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid app key")
    return app_info


def require_scope(app_info: dict, scope: str) -> None:
    if scope not in app_info["scopes"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing scope: {scope}")


def require_device(device_id: str, x_device_token: Annotated[str | None, Header()] = None) -> None:
    if not x_device_token or not store.authenticate(device_id, x_device_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid device token")


def current_user(x_user_token: Annotated[str | None, Header()] = None) -> str:
    user_id = store.session_user(x_user_token or "")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid user token")
    return user_id


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> str:
    if x_admin_key != settings.admin_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin key")
    return "admin"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>森穹小悟 S3 平台后端</title>
<style>body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:720px;margin:48px auto;padding:0 20px;color:#1c2b24;background:#fbfcf8}
h1{font-size:22px}code{background:#eef1e9;padding:2px 6px;border-radius:4px;font-size:13px}
li{margin:6px 0}.tip{background:#eef1e9;border-radius:8px;padding:12px 16px}</style></head><body>
<h1>森穹小悟 S3 平台 · API 后端</h1>
<p>这是纯 API 服务，没有管理界面。请在浏览器打开 <b>PC 后台前端</b>（Next.js）：</p>
<div class="tip">cd apps/pc-console &amp;&amp; npm run dev &rarr; <code>http://127.0.0.1:8101</code><br>
内容配置页面：<code>http://127.0.0.1:8101/content-config</code></div>
<h3>常用接口</h3>
<ul>
<li>健康检查：<code>GET /health</code></li>
<li>接口文档（Swagger）：<code><a href="/docs">/docs</a></code></li>
<li>小程序内容配置：<code>GET/POST /api/v1/mobile/devices/{id}/content-config</code></li>
<li>PC 后台内容配置：<code>GET/POST /admin/v1/devices/{id}/content-config</code>（Header: <code>X-Admin-Key</code>）</li>
</ul>
</body></html>"""


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "platform": "xiaowu-s3"}


@app.post("/api/v1/devices/register", status_code=201)
def register(body: DeviceRegister, x_admin_key: Annotated[str | None, Header()] = None) -> dict[str, str]:
    if not settings.dev_mode and x_admin_key != settings.admin_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "factory enrollment required")
    try:
        token = store.register(body.device_id, body.model, body.firmware_version, DeviceCapabilities().model_dump())
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status.HTTP_409_CONFLICT, "device already registered") from exc
        raise
    if settings.dev_mode:
        store.bind_demo_device("demo-user", body.device_id)
    return {"device_id": body.device_id, "device_token": token}


@app.post("/api/v1/devices/{device_id}/heartbeat", status_code=204)
def heartbeat(device_id: str, body: Heartbeat, _: None = Depends(require_device)) -> Response:
    store.heartbeat(device_id, body.model_dump())
    return Response(status_code=204)


@app.get("/api/v1/devices/{device_id}/capabilities")
def capabilities(device_id: str, _: None = Depends(require_device)) -> dict:
    result = store.capabilities(device_id)
    if not result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return result


@app.get("/api/v1/devices/{device_id}/tasks/next")
def next_task(device_id: str, response: Response, _: None = Depends(require_device)) -> dict | None:
    task = store.next_task(device_id)
    if task is None:
        response.status_code = status.HTTP_204_NO_CONTENT
    return task


@app.post("/api/v1/devices/{device_id}/tasks/{task_id}/ack", status_code=204)
def ack(device_id: str, task_id: str, body: TaskAck, _: None = Depends(require_device)) -> Response:
    if not store.ack(device_id, task_id, body.status, body.detail):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return Response(status_code=204)


@app.post("/api/v1/devices/{device_id}/events", status_code=202)
def device_event(device_id: str, body: DeviceEvent, _: None = Depends(require_device)) -> dict[str, str]:
    event_id = store.record_device_event(device_id, body.event_type, body.data, body.occurred_at)
    return {"event_id": event_id, "status": "accepted"}


@app.post("/openapi/v1/devices/{device_id}/content", status_code=202)
def push_content(device_id: str, body: ContentPush, app_info: dict = Depends(current_app)) -> dict[str, str]:
    require_scope(app_info, "device.content.write")
    template = store.template(body.template)
    if not template:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown or disabled template")
    missing = [field for field in template["schema_definition"].get("required", []) if body.data.get(field) in (None, "", [])]
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "template_validation_failed", "missing": missing})
    task_id = store.create_task(device_id, body.model_dump(exclude={"expires_in_seconds"}), body.expires_in_seconds,
                                source="openapi", created_by=app_info["app_id"])
    if task_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return {"task_id": task_id, "status": "queued"}


@app.get("/openapi/v1/devices/{device_id}")
def openapi_device(device_id: str, app_info: dict = Depends(current_app)) -> dict:
    require_scope(app_info, "device.read")
    capabilities = store.capabilities(device_id)
    if not capabilities:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return {"device_id": device_id, "capabilities": capabilities}


@app.get("/openapi/v1/devices/{device_id}/events")
def openapi_events(device_id: str, app_info: dict = Depends(current_app)) -> list[dict]:
    require_scope(app_info, "device.event.read")
    return store.list_device_events(device_id)


@app.post("/openapi/v1/webhooks", status_code=201)
def create_webhook(body: WebhookCreate, app_info: dict = Depends(current_app)) -> dict[str, object]:
    require_scope(app_info, "webhook.manage")
    webhook_id, secret = store.create_webhook(app_info["app_id"], body.url, body.events)
    return {"id": webhook_id, "signing_secret": secret, "events": body.events}


@app.get("/openapi/v1/webhooks")
def list_webhooks(app_info: dict = Depends(current_app)) -> list[dict]:
    require_scope(app_info, "webhook.manage")
    return store.list_webhooks(app_info["app_id"])


# 小程序端：只提供用户自助内容管理，不暴露运营能力。
@app.post("/api/v1/mobile/login")
def mobile_login(body: PasswordLogin) -> dict[str, str]:
    result = store.login(body.username, body.password)
    if not result:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
    user_id, token = result
    return {"user_id": user_id, "token": token}


@app.get("/api/v1/mobile/scenes")
def mobile_scenes(locale: str = "zh-CN", _: str = Depends(current_user)) -> list[dict]:
    templates = store.list_templates(enabled_only=True)
    names = EN_TEMPLATE_NAMES if locale.lower().startswith("en") else {}
    return [{"id": item["template_id"], "name": names.get(item["template_id"], item["name"]), "category": item["category"],
             "schema": item["schema_definition"]} for item in templates]


@app.get("/api/v1/mobile/devices")
def mobile_devices(user_id: str = Depends(current_user)) -> list[dict]:
    return store.user_devices(user_id)


@app.post("/api/v1/mobile/devices/{device_id}/content", status_code=202)
def mobile_push(device_id: str, body: MobileContentPush, user_id: str = Depends(current_user)) -> dict[str, str]:
    if not store.user_owns(user_id, device_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "device is not bound to this user")
    task_id = store.create_task(device_id, body.model_dump(exclude={"expires_in_seconds", "title"}),
                                body.expires_in_seconds, source="miniapp", created_by=user_id)
    assert task_id
    store.audit(user_id, "content.push", device_id, {"task_id": task_id, "template": body.template})
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/v1/mobile/devices/{device_id}/history")
def mobile_history(device_id: str, user_id: str = Depends(current_user)) -> list[dict]:
    if not store.user_owns(user_id, device_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "device is not bound to this user")
    return store.task_history(device_id, 30)


# 双屏内容配置：小程序与 PC 后台读写同一份 dual_config，互不覆盖。
@app.get("/api/v1/mobile/devices/{device_id}/content-config")
def mobile_content_config(device_id: str, user_id: str = Depends(current_user)) -> dict:
    if not store.user_owns(user_id, device_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "device is not bound to this user")
    cfg = store.get_content_config(device_id)
    return cfg or {"config": None, "updated_at": None}


@app.post("/api/v1/mobile/devices/{device_id}/content-config", status_code=201)
def mobile_save_content_config(device_id: str, body: ContentConfigIn, user_id: str = Depends(current_user)) -> dict:
    if not store.user_owns(user_id, device_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "device is not bound to this user")
    ts = store.save_content_config(device_id, body.payload)
    return {"status": "saved", "updated_at": ts}


# PC 运营后台：全局设备、内容模板、任务、插件与审计都仅在这里开放。
@app.get("/admin/v1/overview")
def admin_overview(_: str = Depends(require_admin)) -> dict:
    return store.overview()


@app.get("/admin/v1/devices")
def admin_devices(_: str = Depends(require_admin)) -> list[dict]:
    return store.all_devices()


@app.get("/admin/v1/tasks")
def admin_tasks(_: str = Depends(require_admin)) -> list[dict]:
    return store.task_history(limit=200)


@app.get("/admin/v1/templates")
def admin_templates(_: str = Depends(require_admin)) -> list[dict]:
    return store.list_templates()


@app.put("/admin/v1/templates/{template_id}", status_code=204)
def admin_template_save(template_id: str, body: TemplateUpsert, actor: str = Depends(require_admin)) -> Response:
    if template_id != body.template_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "template id mismatch")
    store.upsert_template(body.model_dump(), actor)
    return Response(status_code=204)


@app.get("/admin/v1/audit-logs")
def admin_audits(_: str = Depends(require_admin)) -> list[dict]:
    return store.audits()


@app.get("/admin/v1/plugins")
def admin_plugins(_: str = Depends(require_admin)) -> list[dict]:
    return [manifest.public_dict() for manifest in plugin_registry.load()]


@app.post("/admin/v1/developer-apps", status_code=201)
def admin_create_app(body: DeveloperAppCreate, actor: str = Depends(require_admin)) -> dict[str, object]:
    app_id, token = store.create_developer_app(body.name, list(body.scopes))
    store.audit(actor, "developer_app.create", app_id, {"name": body.name, "scopes": body.scopes})
    return {"app_id": app_id, "token": token, "scopes": body.scopes}


@app.get("/admin/v1/developer-apps")
def admin_apps(_: str = Depends(require_admin)) -> list[dict]:
    return store.list_developer_apps()


@app.get("/admin/v1/webhook-outbox")
def admin_webhook_outbox(_: str = Depends(require_admin)) -> list[dict]:
    return store.webhook_outbox()


# PC 后台：双屏内容配置读写（与小程序共用同一份数据）。
@app.get("/admin/v1/devices/{device_id}/content-config")
def admin_content_config(device_id: str, _: str = Depends(require_admin)) -> dict:
    cfg = store.get_content_config(device_id)
    return cfg or {"config": None, "updated_at": None}


@app.post("/admin/v1/devices/{device_id}/content-config", status_code=201)
def admin_save_content_config(device_id: str, body: ContentConfigIn, _: str = Depends(require_admin)) -> dict:
    if not store.device_exists(device_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    ts = store.save_content_config(device_id, body.payload)
    return {"status": "saved", "updated_at": ts}


from fastapi.staticfiles import StaticFiles

_upload_dir = settings.database_path.parent / "uploads"
_upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_upload_dir)), name="uploads")


EN_TEMPLATE_NAMES = {
    "notice-card": "General notice", "homework-list": "Homework list",
    "vocabulary-card": "Vocabulary card", "care-reminder": "Care reminder",
    "daily-schedule": "Daily schedule",
}
