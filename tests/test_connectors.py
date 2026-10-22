"""连接器模块测试：注册表加载、凭据加密、绑定 CRUD 与 OAuth state 一次性消费。"""
import json
from pathlib import Path

from xiaowu_platform.connectors import ConnectorRegistry, CredentialVault
from xiaowu_platform.store import Store

REPO = Path(__file__).resolve().parents[1]


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "connectors.db")
    store.init()
    return store


def test_registry_loads_builtin_connectors():
    registry = ConnectorRegistry(REPO / "connectors")
    items = registry.load()
    assert "demo-oauth" in items and "market-data" in items
    demo = registry.get("demo-oauth")
    assert demo["type"] == "user"
    assert registry.get("demo-oauth")["auth"]["kind"] == "oauth2"
    # 平台级连接器对外不暴露 bind_url 逻辑由 API 层处理，这里只验 manifest
    assert registry.get("market-data")["type"] == "platform"


def test_vault_roundtrip_and_wrong_key():
    vault = CredentialVault("master-key-a")
    token = vault.encrypt({"access_token": "secret", "expires_in": 3600})
    assert "secret" not in token
    assert vault.decrypt(token)["access_token"] == "secret"
    try:
        CredentialVault("master-key-b").decrypt(token)
        assert False, "错误主密钥应解密失败"
    except ValueError:
        pass


def test_connector_binding_crud(tmp_path: Path):
    store = _store(tmp_path)
    vault = CredentialVault("test-master")
    enc = vault.encrypt({"access_token": "at-1"})
    ts = store.save_connector_binding("demo-user", "demo-oauth", enc, "demo@x", ["demo.read"])
    assert ts
    row = store.get_connector_binding("demo-user", "demo-oauth")
    assert row and row["status"] == "active"
    assert vault.decrypt(row["credential_encrypted"])["access_token"] == "at-1"
    # 重复绑定 = 更新
    store.save_connector_binding("demo-user", "demo-oauth", vault.encrypt({"access_token": "at-2"}), "demo@x", [])
    row = store.get_connector_binding("demo-user", "demo-oauth")
    assert vault.decrypt(row["credential_encrypted"])["access_token"] == "at-2"
    assert len(store.list_connector_bindings("demo-user")) == 1
    # 解绑
    assert store.delete_connector_binding("demo-user", "demo-oauth")
    assert store.get_connector_binding("demo-user", "demo-oauth") is None
    assert not store.delete_connector_binding("demo-user", "demo-oauth")


def test_connector_state_single_use(tmp_path: Path):
    store = _store(tmp_path)
    state = store.create_connector_state("demo-user", "demo-oauth")
    first = store.consume_connector_state(state)
    assert first == {"user_id": "demo-user", "connector_id": "demo-oauth"}
    assert store.consume_connector_state(state) is None  # 一次性
    assert store.consume_connector_state("never-existed") is None
