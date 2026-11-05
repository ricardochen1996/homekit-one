from __future__ import annotations

from pathlib import Path
from typing import Any

from homekit_one.config import MijiaConfig, MijiaDeviceConfig, TreeowConfig
from homekit_one.core.model import Capability, Command, DeviceKind, Mode
from homekit_one.providers.mijia.provider import MijiaProvider
from homekit_one.providers.treeow.provider import TreeowProvider
from homekit_one.registry import DeviceRegistry
from homekit_one.secrets import SecretsStore


def _secrets(tmp_path: Path) -> SecretsStore:
    return SecretsStore(tmp_path / "secrets.json")


class FakeMiio:
    def __init__(self, raw: dict[tuple[int, int], Any]) -> None:
        self.host = "127.0.0.1"
        self.token = b"\x01" * 16
        self.raw = raw
        self.writes: list[tuple[int, int, Any]] = []

    async def get_properties(self, props):
        return {key: value for key, value in self.raw.items() if key in props}

    async def set_properties(self, writes):
        self.writes.extend(writes)


async def test_mijia_provider_refresh_and_execute(tmp_path):
    config = MijiaConfig(
        enabled=True,
        devices=(
            MijiaDeviceConfig(
                id="123",
                name="Purifier",
                model="zhimi.airpurifier.rma1",
                host="127.0.0.1",
                token="01" * 16,
            ),
        ),
    )
    provider = MijiaProvider(config, _secrets(tmp_path), tmp_path, DeviceRegistry(tmp_path / "d.json"))
    devices = await provider.list_devices()
    assert len(devices) == 1
    device = devices[0]
    assert device.kind is DeviceKind.PURIFIER
    assert Capability.PM25 in device.capabilities

    fake = FakeMiio({(2, 1): True, (2, 4): 0, (9, 2): 14, (3, 4): 8})
    provider._clients[device.id] = fake  # type: ignore[assignment]
    values = await provider.refresh(device)
    assert values[Capability.POWER] is True
    assert values[Capability.MODE] == Mode.AUTO.value
    assert values[Capability.FAN_SPEED] == 100
    assert values[Capability.PM25] == 8.0

    await provider.execute(device, Command(Capability.POWER, False))
    assert fake.writes == [(2, 1, 0)]


class FakeTreeowAPI:
    def __init__(self) -> None:
        self.controls: list[tuple[str, Any]] = []
        self.logged_in = False

    async def login(self):
        self.logged_in = True

    async def get_homes(self):
        return [{"homeGroups": [{"id": 7}]}]

    async def get_device_list(self, group_id: str):
        return [{"deviceSerial": "T2-1", "deviceName": "客厅树新风", "model": "T2Pro"}]

    async def get_device_info(self, serial: str):
        import json

        return {
            "props": [
                {
                    "value": json.dumps(
                        {
                            "Air_Purifier": {
                                "switch": 1,
                                "mode": 1,
                                "fan_speed_enum": 6,
                                "pm25": 9,
                                "temp_indoor": 22,
                                "humidity": 45,
                                "filter_life": 88,
                                "child_lock": 0,
                                "light": 1,
                            }
                        }
                    )
                }
            ]
        }

    async def control(self, serial: str, prop: str, value: Any):
        self.controls.append((prop, value))

    async def close(self):
        return None


async def test_treeow_provider(tmp_path):
    config = TreeowConfig(enabled=True, account="a", password="p")
    provider = TreeowProvider(config, _secrets(tmp_path), tmp_path)
    fake = FakeTreeowAPI()
    provider.api = fake  # type: ignore[assignment]
    await provider.start()
    devices = await provider.list_devices()
    assert len(devices) == 1
    device = devices[0]
    assert device.kind is DeviceKind.PURIFIER

    values = await provider.refresh(device)
    assert values[Capability.POWER] is True
    assert values[Capability.MODE] == Mode.AUTO.value
    assert values[Capability.FAN_SPEED] == 100
    assert values[Capability.PM25] == 9.0

    await provider.execute(device, Command(Capability.DISPLAY, False))
    assert fake.controls == [("light", False)]
