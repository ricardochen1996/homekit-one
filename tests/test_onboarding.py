from __future__ import annotations

from typing import Any

from homekit_one.config import Config, GatewayConfig
from homekit_one.core.model import Capability, Device, DeviceKind
from homekit_one.providers import onboarding
from homekit_one.providers.onboarding import probe_mijia_device, probe_provider
from homekit_one.providers.plugins import ProviderPlugin
from homekit_one.secrets import SecretsStore


def _patch_plugin(monkeypatch, name, provider_cls):
    factory = lambda config, secrets, state_dir, registry: provider_cls(  # noqa: E731
        config, secrets, state_dir
    )
    plugin = ProviderPlugin(name=name, label=name, factory=factory)
    monkeypatch.setattr(
        onboarding, "get_plugin", lambda requested: plugin if requested == name else None
    )


def _config(tmp_path):
    return Config(gateway=GatewayConfig(state_dir=tmp_path), path=tmp_path / "config.toml")


def _device() -> Device:
    return Device(
        id="treeow:1",
        name="客厅空调",
        provider="treeow",
        kind=DeviceKind.AC,
        model="Split AC",
        capabilities={Capability.POWER},
        online=True,
    )


class _FakeProvider:
    name = "treeow"

    def __init__(self, config, secrets, state_dir) -> None:
        self.config = config
        self.last_error: str | None = None
        self.stopped = False

    async def start(self) -> None:
        return None

    async def list_devices(self) -> list[Device]:
        return [_device()]

    async def refresh(self, device: Device) -> dict[Any, Any]:
        return {Capability.POWER: True}

    async def stop(self) -> None:
        self.stopped = True


async def test_probe_provider_reports_devices(tmp_path, monkeypatch):
    _patch_plugin(monkeypatch, "treeow", _FakeProvider)
    secrets = SecretsStore(tmp_path / "secrets.json")
    report = await probe_provider("treeow", _config(tmp_path), secrets, tmp_path)
    assert report.ok is True
    assert report.level == "ok"
    assert report.reachable == 1
    assert report.devices[0].name == "客厅空调"
    assert report.devices[0].state["power"] == "开"
    assert "验证成功" in report.summary


class _NoDeviceProvider(_FakeProvider):
    async def list_devices(self) -> list[Device]:
        return []


async def test_probe_provider_without_devices_is_warning(tmp_path, monkeypatch):
    _patch_plugin(monkeypatch, "treeow", _NoDeviceProvider)
    secrets = SecretsStore(tmp_path / "secrets.json")
    report = await probe_provider("treeow", _config(tmp_path), secrets, tmp_path)
    assert report.ok is True
    assert report.level == "warn"
    assert "没有发现" in report.summary
    assert "Treeow Home" in report.hint


class _AuthFailProvider(_FakeProvider):
    def __init__(self, config, secrets, state_dir) -> None:
        super().__init__(config, secrets, state_dir)
        self.cloud = object()
        self.last_error = "小米账号拒绝密码登录（403）"

    async def start(self) -> None:
        return None

    async def list_devices(self) -> list[Device]:
        raise AssertionError("list should not run after an auth error")


async def test_probe_provider_maps_auth_error_to_oauth_hint(tmp_path, monkeypatch):
    _patch_plugin(monkeypatch, "mijia_cloud", _AuthFailProvider)
    secrets = SecretsStore(tmp_path / "secrets.json")
    report = await probe_provider("mijia_cloud", _config(tmp_path), secrets, tmp_path)
    assert report.ok is False
    assert report.level == "bad"
    assert "OAuth" in report.hint


class _MissingCredentialsProvider(_FakeProvider):
    cloud = None


async def test_probe_provider_without_credentials(tmp_path, monkeypatch):
    _patch_plugin(monkeypatch, "treeow", _MissingCredentialsProvider)
    report = await probe_provider(
        "treeow", _config(tmp_path), SecretsStore(tmp_path / "secrets.json"), tmp_path
    )
    assert report.ok is False
    assert "没有保存凭据" in report.summary


class _FakeMiio:
    device_id = 12345

    def __init__(self, host: str, token: str, timeout: float = 4.0) -> None:
        self.host = host
        self.token = token

    async def get_properties(self, props):
        return {(2, 1): 1, (2, 4): 0, (9, 2): 14, (3, 4): 8}


async def test_probe_mijia_device_success(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "MiioClient", _FakeMiio)
    report = await probe_mijia_device(
        "12345", "192.168.1.5", "0" * 32, "zhimi.airpurifier.rma1", name="净化器"
    )
    assert report.ok is True
    assert report.level == "ok"
    assert report.devices[0].state["power"] == "开"
    assert report.devices[0].state["pm25"] == 8.0


async def test_probe_mijia_device_did_mismatch_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "MiioClient", _FakeMiio)
    report = await probe_mijia_device(
        "99999", "192.168.1.5", "0" * 32, "zhimi.airpurifier.rma1"
    )
    assert report.ok is True
    assert report.warnings and "12345" in report.warnings[0]


class _BadTokenMiio(_FakeMiio):
    async def get_properties(self, props):
        raise RuntimeError("token 校验失败")


async def test_probe_mijia_device_bad_token(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding, "MiioClient", _BadTokenMiio)
    report = await probe_mijia_device(
        "12345", "192.168.1.5", "1" * 32, "zhimi.airpurifier.rma1"
    )
    assert report.ok is False
    assert "token" in report.hint.lower()


async def test_probe_mijia_device_unsupported_model():
    report = await probe_mijia_device(
        "1", "192.168.1.5", "0" * 32, "unknown.model"
    )
    assert report.ok is False
    assert "暂不支持" in report.summary
