from __future__ import annotations

import json
from typing import Any

from homeone.config import TclPlusConfig
from homeone.core.model import Capability, Command, DeviceKind
from homeone.providers.tcl_plus.mapping import (
    decode_tv_status,
    encode_tv_command,
    is_tv,
    parse_sources,
)
from homeone.providers.tcl_plus.provider import TclPlusProvider
from homeone.secrets import SecretsStore


def _status() -> dict[str, Any]:
    sources = json.dumps(
        [
            json.dumps({"source": "TV"}),
            json.dumps({"source": "HDMI1"}),
            json.dumps({"source": "HDMI2"}),
        ]
    )
    return {
        "capabilities": [1],
        "mute": 0,
        "powerSwitch": 1,
        "source": "HDMI1",
        "sourceList": sources,
        "tvStatus": 0,
        "volume": 4,
    }


def test_tv_mapping():
    status = _status()
    assert is_tv("TV")
    assert not is_tv("AC")
    assert parse_sources(status) == ["TV", "HDMI1", "HDMI2"]
    values = decode_tv_status(status)
    assert values[Capability.POWER] is True
    assert values[Capability.VOLUME] == 4
    assert values[Capability.MUTE] is False
    assert values[Capability.INPUT] == "HDMI1"
    assert encode_tv_command(Command(Capability.POWER, False)) == {"powerSwitch": 0}
    assert encode_tv_command(Command(Capability.VOLUME, 33)) == {"volume": 33}
    assert encode_tv_command(Command(Capability.MUTE, True)) == {"mute": 1}
    assert encode_tv_command(Command(Capability.INPUT, "HDMI2")) == {"source": "HDMI2"}


class FakeTclPlusClient:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.status = _status()

    async def login(self) -> None:
        return None

    async def get_devices(self):
        return [
            {
                "deviceId": "12345678",
                "nickName": "测试电视",
                "category": "TV",
                "productKey": "test-product-key",
                "isOnline": 1,
                "weChatControl": "0",
            }
        ]

    async def get_snapshot(self, device_id: str):
        return self.status

    async def send_command(self, device_id: str, attributes: dict[str, Any]):
        self.commands.append((device_id, attributes))
        return {"code": "200"}

    async def close(self) -> None:
        return None


async def test_tcl_plus_provider_and_homekit(tmp_path):
    from homeone.config import Config, FakeConfig, GatewayConfig
    from homeone.core.manager import DeviceManager
    from homeone.homekit.bridge import HomeKitBridge

    config = Config(
        gateway=GatewayConfig(
            state_dir=tmp_path,
            homekit_port=51990,
            poll_intervals={"tcl_plus": 0.05, "fake": 0.05},
        ),
        fake=FakeConfig(enabled=False),
        tcl_plus=TclPlusConfig(enabled=True, phone="13800138000", password="p"),
    )
    provider = TclPlusProvider(
        config.tcl_plus, SecretsStore(tmp_path / "s.json"), tmp_path
    )
    fake = FakeTclPlusClient()
    provider.client = fake  # type: ignore[assignment]
    await provider.start()

    devices = await provider.list_devices()
    assert len(devices) == 1
    tv = devices[0]
    assert tv.kind is DeviceKind.TV
    assert tv.meta["sources"] == ["TV", "HDMI1", "HDMI2"]
    assert {
        Capability.POWER,
        Capability.VOLUME,
        Capability.MUTE,
        Capability.INPUT,
    } <= tv.capabilities

    values = await provider.refresh(tv)
    assert values[Capability.INPUT] == "HDMI1"
    await provider.execute(tv, Command(Capability.INPUT, "HDMI2"))
    assert fake.commands == [("12345678", {"source": "HDMI2"})]

    manager = DeviceManager(config, [provider])
    await manager.start()
    bridge = HomeKitBridge(config, manager)
    try:
        service_types = {
            service["type"]
            for accessory in bridge.bridge.to_HAP()
            for service in accessory["services"]
        }
        assert "D8" in service_types  # Television
        assert "113" in service_types  # TelevisionSpeaker
        assert "D9" in service_types  # InputSource
    finally:
        await bridge.stop()
        await manager.stop()
