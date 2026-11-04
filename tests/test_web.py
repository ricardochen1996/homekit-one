from __future__ import annotations

import httpx

from homeone.config import Config, FakeConfig, GatewayConfig
from homeone.core.manager import DeviceManager
from homeone.core.model import Capability
from homeone.homekit.bridge import HomeKitBridge
from homeone.providers.fake import FakeProvider
from homeone.web.server import create_app


class _GatewayStub:
    def __init__(self, tmp_path):
        self.config = Config(
            gateway=GatewayConfig(
                state_dir=tmp_path,
                poll_intervals={"fake": 0.05, "mijia": 1, "tcl": 1, "treeow": 1},
            ),
            fake=FakeConfig(enabled=True),
            path=tmp_path / "config.toml",
        )
        from homeone.registry import DeviceRegistry
        from homeone.secrets import SecretsStore

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
