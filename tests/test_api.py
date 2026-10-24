from pathlib import Path

from xiaowu_platform.models import ContentPush, DeviceCapabilities
from xiaowu_platform.store import Store
from xiaowu_platform.plugin_runtime import PluginRegistry


def test_device_content_flow(tmp_path: Path):
    store = Store(tmp_path / "test.db")
    store.init()
    capabilities = DeviceCapabilities().model_dump()
    token = store.register("SIM-S3-TEST", "xiaowu-s3-dual", "test", capabilities)
    assert store.authenticate("SIM-S3-TEST", token)
    store.heartbeat("SIM-S3-TEST", {
        "firmware_version": "test", "battery_voltage": 4.0,
        "rssi": -50, "runtime_mode": "active",
    })
    payload = ContentPush(template="notice-card", screen="A", data={"title": "Hello"})
    task_id = store.create_task(
        "SIM-S3-TEST", payload.model_dump(exclude={"expires_in_seconds"}),
        payload.expires_in_seconds,
    )
    assert task_id
    task = store.next_task("SIM-S3-TEST")
    assert task and task["payload"]["data"]["title"] == "Hello"
    assert store.ack("SIM-S3-TEST", task_id, "displayed", None)
    assert store.next_task("SIM-S3-TEST") is None


def test_wrong_device_token_is_rejected(tmp_path: Path):
    store = Store(tmp_path / "auth.db")
    store.init()
    store.register("SIM-S3-AUTH", "xiaowu-s3-dual", "test", DeviceCapabilities().model_dump())
    assert not store.authenticate("SIM-S3-AUTH", "wrong")
    assert not store.authenticate("UNKNOWN", "wrong")


def test_s3_capability_contract():
    capabilities = DeviceCapabilities()
    assert capabilities.model == "xiaowu-s3-dual"
    assert [(screen.id, screen.width, screen.height) for screen in capabilities.screens] == [
        ("A", 400, 300), ("B", 400, 300)
    ]
    assert capabilities.audio_input and capabilities.audio_output


def test_mobile_and_operations_views(tmp_path: Path):
    store = Store(tmp_path / "operations.db")
    store.init()
    store.register("S3-OPS", "xiaowu-s3-dual", "0.1", DeviceCapabilities().model_dump())
    store.bind_demo_device("user-1", "S3-OPS", "书房双屏")
    assert store.user_owns("user-1", "S3-OPS")
    assert store.user_devices("user-1")[0]["nickname"] == "书房双屏"
    assert {item["template_id"] for item in store.list_templates(True)} >= {"homework-list", "care-reminder"}
    task_id = store.create_task("S3-OPS", {"template": "homework-list", "screen": "A", "data": {}}, 3600,
                                source="miniapp", created_by="user-1")
    assert task_id
    assert store.overview()["devices"] == 1
    assert store.task_history()[0]["source"] == "miniapp"


def test_developer_app_events_and_webhook_outbox(tmp_path: Path):
    store = Store(tmp_path / "developer.db")
    store.init()
    store.register("S3-EVENT", "xiaowu-s3-dual", "0.1", DeviceCapabilities().model_dump())
    app_id, token = store.create_developer_app("School connector", ["device.event.read", "webhook.manage"])
    app = store.authenticate_app(token)
    assert app and app["app_id"] == app_id
    webhook_id, secret = store.create_webhook(app_id, "https://example.com/xiaowu", ["device.event"])
    assert webhook_id.startswith("wh-") and secret
    event_id = store.record_device_event("S3-EVENT", "button.pressed", {"button": "confirm"}, None)
    assert event_id.startswith("evt-")
    assert store.list_device_events("S3-EVENT")[0]["data"]["button"] == "confirm"
    assert store.webhook_outbox()[0]["status"] == "pending"


def test_plugin_manifests_match_s3(tmp_path: Path):
    registry = PluginRegistry(Path(__file__).parents[1] / "plugins")
    plugins = registry.load()
    assert {p.plugin_id for p in plugins} == {"education-homework", "elder-care-reminder"}
    for plugin in plugins:
        supported, missing = plugin.supports(DeviceCapabilities().model_dump())
        assert supported, missing


def test_developer_sandbox_review_and_publish_flow(tmp_path: Path):
    store = Store(tmp_path / "creator.db")
    store.init()
    developer_id, session = store.register_developer(
        "创作者", "测试团队", "creator@example.com", "safe-pass-123", "2026-10-23", "content_creator"
    )
    developer = store.developer_by_session(session)
    assert developer and developer["developer_id"] == developer_id
    assert developer["role"] == "content_creator"

    app, sandbox_token = store.create_sandbox_app(
        developer_id, "墨水日程", "office", "把会议与专注时间放到双屏"
    )
    assert sandbox_token and app["status"] == "sandbox"
    assert store.submit_sandbox_app(app["app_id"], developer_id, "0.1.0", "首个版本")
    assert store.sandbox_app(app["app_id"])["status"] == "in_review"
    assert store.review_sandbox_app(app["app_id"], "approve", "内容与刷新频率合规")
    assert store.sandbox_app(app["app_id"])["status"] == "published"


def test_developer_model_config_never_returns_secret(tmp_path: Path):
    store = Store(tmp_path / "model.db")
    store.init()
    developer_id, _ = store.register_developer(
        "模型开发者", "", "model@example.com", "safe-pass-123", "2026-10-23"
    )
    app, _ = store.create_sandbox_app(developer_id, "AI 晨报", "custom", "")
    store.save_model_config(app["app_id"], "deepseek", "deepseek-chat", "", "encrypted-secret", "简洁输出")
    config = store.get_model_config(app["app_id"])
    assert config and config["api_key_configured"] is True
    assert "credential_encrypted" not in config


def test_demo_admin_can_view_all_creators_and_apps(tmp_path: Path):
    store = Store(tmp_path / "admin.db")
    store.init()
    store.ensure_admin("test-admin", "test-only-password")
    result = store.login_admin("test-admin", "test-only-password")
    assert result
    admin, token = result
    assert admin["username"] == "test-admin"
    assert store.admin_by_session(token)["username"] == "test-admin"
    developer_id, _ = store.register_developer(
        "IP 主理人", "小悟内容社", "ip@example.com", "safe-pass-123", "2026-10-23", "ip_owner"
    )
    app, _ = store.create_sandbox_app(developer_id, "城市文化日历", "custom", "每日城市文化内容")
    assert store.list_developer_accounts()[0]["role"] == "ip_owner"
    assert store.list_sandbox_apps()[0]["name"] == app["name"]
