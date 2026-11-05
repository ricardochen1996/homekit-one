from __future__ import annotations

import tomllib
from urllib.parse import unquote

import httpx

from homekit_one.config import Config, FakeConfig, GatewayConfig
from homekit_one.core.manager import DeviceManager
from homekit_one.core.model import Capability
from homekit_one.homekit.bridge import HomeKitBridge
from homekit_one.providers.fake import FakeProvider
from homekit_one.providers.mijia.discovery import DiscoveredDevice
from homekit_one.providers.onboarding import DeviceReport, ProviderReport
from homekit_one.web.server import create_app


class _GatewayStub:
    def __init__(self, tmp_path):
        self.config = Config(
            gateway=GatewayConfig(
                state_dir=tmp_path,
                poll_intervals={"fake": 0.05, "mijia": 1, "treeow": 1},
            ),
            fake=FakeConfig(enabled=True),
            path=tmp_path / "config.toml",
        )
        from homekit_one.registry import DeviceRegistry
        from homekit_one.secrets import SecretsStore

        self.secrets = SecretsStore(tmp_path / "secrets.json")
        self.registry = DeviceRegistry(tmp_path / "devices.json")
        self.manager = DeviceManager(self.config, [FakeProvider()])
        self.bridge = None

    def provider_status(self):
        return [{"name": "fake", "devices": 4, "last_error": None}]

    def pairing_info(self):
        return self.bridge.pairing_info() if self.bridge else None

    def request_restart(self):
        pass


async def _client(tmp_path):
    gateway = _GatewayStub(tmp_path)
    await gateway.manager.start()
    app = create_app(gateway)
    return gateway, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_index_and_health(tmp_path):
    gateway, client = await _client(tmp_path)
    try:
        response = await client.get("/")
        assert response.status_code == 200
        assert "演示净化器" in response.text
        health = (await client.get("/api/health")).json()
        assert health["ok"] is True
        assert health["devices"] == 4
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_device_api_set(tmp_path):
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/api/devices/fake:purifier/set",
            json={"capability": "power", "value": False},
        )
        assert response.status_code == 200
        assert response.json()["state"]["power"] is False
        assert gateway.manager.get("fake:purifier").get(Capability.POWER) is False
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_setup_adds_mijia_device(tmp_path):
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/mijia",
            data={
                "device_id": "12345",
                "name": "测试净化器",
                "model": "zhimi.airpurifier.rma1",
                "host": "192.168.1.5",
                "token": "0" * 32,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        entries = gateway.registry.mijia_entries()
        assert entries[0]["id"] == "12345"
        assert gateway.secrets.get("mijia", "tokens")["12345"] == "0" * 32
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_setup_cloud_credentials(tmp_path):
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/mijia-cloud",
            data={"username": "u", "password": "p", "country": "cn"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert gateway.secrets.get("mijia_cloud", "username") == "u"
        assert gateway.secrets.get("mijia_cloud", "country") == "cn"
        assert "mijia_cloud" in (tmp_path / "config.toml").read_text("utf-8")
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_pairing_pages(tmp_path):
    gateway, client = await _client(tmp_path)
    try:
        gateway.bridge = HomeKitBridge(gateway.config, gateway.manager)
        page = await client.get("/pairing")
        assert page.status_code == 200
        png = await client.get("/pairing/qr.png")
        assert png.status_code == 200
        assert png.headers["content-type"] == "image/png"
        assert png.content.startswith(b"\x89PNG")
        await gateway.bridge.stop()
    finally:
        await client.aclose()
        await gateway.manager.stop()


def _report(provider: str, *, ok=True, level_devices=True) -> ProviderReport:
    return ProviderReport(
        provider=provider,
        label=provider,
        ok=ok,
        summary="验证成功：发现 1 台设备" if ok else "登录失败",
        hint="" if ok else "检查账号密码",
        warnings=[] if ok else ["认证失败"],
        devices=[
            DeviceReport(
                id="1",
                name="测试设备",
                kind="purifier",
                model="test",
                online=True,
                reachable=True,
                state={"power": "开"},
            )
        ]
        if level_devices
        else [],
    )


async def test_setup_verify_runs_probe(tmp_path, monkeypatch):
    async def fake_probe(provider, config, secrets, state_dir, registry=None):
        return _report(provider)

    monkeypatch.setattr("homekit_one.web.server.probe_provider", fake_probe)
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/treeow",
            data={"account": "a", "password": "p", "verify": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert gateway.setup_reports["treeow"].ok is True
        assert "验证成功" in unquote(response.headers["location"])
        assert gateway.setup_dirty is True
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_setup_verify_failure_is_reported(tmp_path, monkeypatch):
    async def fake_probe(provider, config, secrets, state_dir, registry=None):
        return _report(provider, ok=False)

    monkeypatch.setattr("homekit_one.web.server.probe_provider", fake_probe)
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/treeow",
            data={"account": "a", "password": "p", "verify": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = unquote(response.headers["location"])
        assert "error=" in location
        assert "验证失败" in location
        assert gateway.setup_reports["treeow"].ok is False
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_provider_verify_and_disable(tmp_path, monkeypatch):
    async def fake_probe(provider, config, secrets, state_dir, registry=None):
        return _report(provider)

    monkeypatch.setattr("homekit_one.web.server.probe_provider", fake_probe)
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/treeow/verify", follow_redirects=False
        )
        assert response.status_code == 303
        assert gateway.setup_reports["treeow"].ok is True

        response = await client.post(
            "/setup/treeow/disable", follow_redirects=False
        )
        assert response.status_code == 303
        data = tomllib.loads(gateway.config.path.read_text("utf-8"))
        assert data["providers"]["treeow"]["enabled"] is False

        response = await client.post(
            "/setup/treeow/enable", follow_redirects=False
        )
        assert response.status_code == 303
        data = tomllib.loads(gateway.config.path.read_text("utf-8"))
        assert data["providers"]["treeow"]["enabled"] is True

        response = await client.post(
            "/setup/treeow/devices",
            data={"devices": ["T2-1", "T2-2"]},
            follow_redirects=False,
        )
        assert response.status_code == 303
        data = tomllib.loads(gateway.config.path.read_text("utf-8"))
        assert data["providers"]["treeow"]["devices"] == ["T2-1", "T2-2"]
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_mijia_add_with_verify(tmp_path, monkeypatch):
    async def fake_probe_device(device_id, host, token, model, *, name=""):
        report = _report("mijia")
        report.devices[0].id = device_id
        report.devices[0].name = name or device_id
        return report

    monkeypatch.setattr(
        "homekit_one.web.server.probe_mijia_device", fake_probe_device
    )
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/mijia",
            data={
                "device_id": "12345",
                "name": "测试净化器",
                "model": "zhimi.airpurifier.rma1",
                "host": "192.168.1.5",
                "token": "0" * 32,
                "verify": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert gateway.setup_reports["mijia"].ok is True
        assert "验证成功" in unquote(response.headers["location"])
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_mijia_discovery_lists_devices(tmp_path, monkeypatch):
    async def fake_discover(duration=3.0, rounds=3):
        return [
            DiscoveredDevice(
                host="192.168.1.9", device_id=0x12345678, token="ab" * 16
            )
        ]

    monkeypatch.setattr(
        "homekit_one.providers.mijia.discovery.discover", fake_discover
    )
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/mijia/discover", follow_redirects=False
        )
        assert response.status_code == 303
        assert gateway.mijia_discovery[0]["host"] == "192.168.1.9"
        page = await client.get("/setup")
        assert "192.168.1.9" in page.text
        assert "已获取" in page.text
    finally:
        await client.aclose()
        await gateway.manager.stop()


class _FakeOAuthClient:
    def __init__(
        self,
        country,
        state_dir=None,
        tokens=None,
        device_id=None,
        redirect_uri=None,
        client_id="test",
        on_tokens=None,
    ) -> None:
        self.device_id = device_id
        self.redirect_uri = redirect_uri
        self.code = ""

    def auth_url(self) -> str:
        return "https://account.xiaomi.com/oauth2/authorize?client_id=test"

    async def exchange_code(self, code: str) -> dict:
        self.code = code
        return {}

    def tokens(self) -> dict:
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 0,
            "device_id": self.device_id,
            "redirect_uri": self.redirect_uri,
            "client_id": "test",
        }

    async def close(self) -> None:
        return None


async def test_mijia_oauth_page_and_callback(tmp_path, monkeypatch):
    async def fake_probe(provider, config, secrets, state_dir, registry=None):
        return _report(provider)

    monkeypatch.setattr("homekit_one.web.server.XiaomiOAuthClient", _FakeOAuthClient)
    monkeypatch.setattr("homekit_one.web.server.probe_provider", fake_probe)
    gateway, client = await _client(tmp_path)
    try:
        gateway.secrets.set_many("mijia_oauth", {"device_id": "dev-1"})
        page = await client.get("/setup/mijia-cloud/oauth?country=cn")
        assert page.status_code == 200
        assert "account.xiaomi.com" in page.text

        response = await client.post(
            "/setup/mijia-cloud/oauth/callback",
            data={
                "callback": "http://homeassistant.local:8123/?code=abc",
                "device_id": "dev-1",
                "country": "cn",
                "redirect_uri": "http://homeassistant.local:8123",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert gateway.secrets.get("mijia_oauth", "refresh_token") == "refresh"
        assert gateway.secrets.get("mijia_cloud", "country") == "cn"
        assert gateway.setup_reports["mijia_cloud"].ok is True
        assert "OAuth 授权成功" in unquote(response.headers["location"])
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_mijia_oauth_callback_rejects_bad_state(tmp_path, monkeypatch):
    monkeypatch.setattr("homekit_one.web.server.XiaomiOAuthClient", _FakeOAuthClient)
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/mijia-cloud/oauth/callback",
            data={
                "callback": "http://homeassistant.local:8123/?code=abc&state=wrong",
                "device_id": "dev-1",
                "country": "cn",
                "redirect_uri": "http://homeassistant.local:8123",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "state" in unquote(response.headers["location"])
        assert gateway.secrets.get("mijia_oauth", "refresh_token") is None
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_device_detail_and_html_controls(tmp_path):
    gateway, client = await _client(tmp_path)
    try:
        page = await client.get("/devices/fake:purifier")
        assert page.status_code == 200
        assert "只读参数" in page.text
        assert "配置 / 控制" in page.text

        response = await client.post(
            "/devices/fake:purifier/set",
            data={"capability": "fan_speed", "value": "70"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert gateway.manager.get("fake:purifier").get(Capability.FAN_SPEED) == 70

        response = await client.post(
            "/devices/fake:purifier/refresh", follow_redirects=False
        )
        assert response.status_code == 303

        index = await client.get("/")
        assert "/devices/fake:purifier/refresh" in index.text
        assert "/api/devices/fake:purifier/refresh" not in index.text
    finally:
        await client.aclose()
        await gateway.manager.stop()


async def test_ajax_redirects_return_json(tmp_path):
    gateway, client = await _client(tmp_path)
    try:
        response = await client.post(
            "/setup/treeow",
            data={"account": "a", "password": "p"},
            headers={"X-HomeOne-Ajax": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert "已保存" in payload["message"]

        response = await client.post(
            "/devices/fake:purifier/set",
            data={"capability": "fan_speed", "value": "66"},
            headers={"X-HomeOne-Ajax": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert gateway.manager.get("fake:purifier").get(Capability.FAN_SPEED) == 66
    finally:
        await client.aclose()
        await gateway.manager.stop()
