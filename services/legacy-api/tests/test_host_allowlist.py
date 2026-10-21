"""Host 白名单中间件：拒绝时返回可读 JSON，且不误伤白名单域名。

背景：自托管部署下，Next.js 的 /api 反代会把浏览器的 Host 原样转给后端，
如果 INKSIGHT_ALLOWED_HOSTS 没写 WebApp 域名，前端所有接口都会 400。
这里锁住行为，避免以后又退回 Starlette 那行 "Invalid host header"。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.index import HostAllowlistMiddleware, _build_allowed_hosts


def _app(allowed: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(HostAllowlistMiddleware, allowed_hosts=allowed)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_allows_configured_host():
    client = TestClient(_app(["example.com", "localhost"]))
    assert client.get("/ping", headers={"Host": "example.com"}).status_code == 200


def test_allows_configured_host_with_port():
    client = TestClient(_app(["example.com"]))
    assert client.get("/ping", headers={"Host": "example.com:8091"}).status_code == 200


def test_rejects_unknown_host_with_readable_json():
    client = TestClient(_app(["example.com"]))
    res = client.get("/ping", headers={"Host": "nas.insane.vip"})
    assert res.status_code == 400
    body = res.json()
    assert body["error"] == "host_not_allowed"
    assert body["host"] == "nas.insane.vip"
    assert "INKSIGHT_ALLOWED_HOSTS" in body["message"]
    assert "example.com" in body["allowed_hosts"]


def test_wildcard_allows_everything():
    client = TestClient(_app(["*"]))
    assert client.get("/ping", headers={"Host": "anything.example"}).status_code == 200


def test_subdomain_wildcard():
    client = TestClient(_app(["*.example.com"]))
    assert client.get("/ping", headers={"Host": "api.example.com"}).status_code == 200
    assert client.get("/ping", headers={"Host": "example.com"}).status_code == 400


def test_ipv6_bracket_host():
    client = TestClient(_app(["::1"]))
    assert client.get("/ping", headers={"Host": "[::1]:8090"}).status_code == 200


@pytest.mark.parametrize("env_hosts", ["nas.insane.vip", "nas.insane.vip,localhost"])
def test_env_var_is_merged(monkeypatch, env_hosts):
    monkeypatch.setenv("INKSIGHT_ALLOWED_HOSTS", env_hosts)
    hosts = _build_allowed_hosts()
    assert "nas.insane.vip" in hosts
    # 内置默认值不能被覆盖掉
    assert "localhost" in hosts
    assert "inksight.site" in hosts


@pytest.mark.parametrize("env_hosts", ["", "   "])
def test_fail_open_when_unconfigured(monkeypatch, env_hosts):
    """未显式配置 INKSIGHT_ALLOWED_HOSTS → fail-open，任何 Host 都放行。

    自托管域名千差万别，写死白名单会让整站 /api 400；一旦运维显式配置
    了变量（见 test_env_var_is_merged），必须恢复严格拦截。
    """
    monkeypatch.setenv("INKSIGHT_ALLOWED_HOSTS", env_hosts)
    hosts = _build_allowed_hosts()
    assert "*" in hosts
    client = TestClient(_app(hosts))
    for host in ("nas.insane.vip", "192.168.1.50", "whatever.local:8091"):
        assert client.get("/ping", headers={"Host": host}).status_code == 200


def test_explicit_config_does_not_fail_open(monkeypatch):
    monkeypatch.setenv("INKSIGHT_ALLOWED_HOSTS", "good.example.com")
    hosts = _build_allowed_hosts()
    assert "*" not in hosts
    client = TestClient(_app(hosts))
    assert client.get("/ping", headers={"Host": "nas.insane.vip"}).status_code == 400
