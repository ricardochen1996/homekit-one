from __future__ import annotations

from pathlib import Path
from typing import Any

from homeone.config import MijiaConfig, MijiaDeviceConfig, TclConfig, TreeowConfig
from homeone.core.model import Capability, Command, DeviceKind, Mode
from homeone.providers.mijia.provider import MijiaProvider
from homeone.providers.tcl.provider import TclProvider
from homeone.providers.treeow.provider import TreeowProvider
from homeone.registry import DeviceRegistry
from homeone.secrets import SecretsStore


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


class FakeTclCloud:
    def __init__(self) -> None:
        self.desired: list[tuple[str, dict[str, Any]]] = []

    async def ensure_session(self):
        return None

    async def get_things(self):
        return [
            {
                "device_id": "ac1",
                "device_name": "Split AC",
                "nick_name": "客厅空调",
                "is_online": 1,
            },
            {"device_id": "pur1", "device_name": "breeva A2", "is_online": 1},
        ]

    async def get_shadow(self, device_id: str):
        return {
            "state": {
                "reported": {
                    "powerSwitch": 1,
                    "targetTemperature": 24,
                    "currentTemperature": 26,
                    "workMode": 1,
                    "windSpeed": 4,
                    "verticalSwitch": 0,
                }
            }
        }

    async def set_desired(self, device_id: str, desired: dict[str, Any]):
        self.desired.append((device_id, desired))

    async def close(self):
        return None


async def test_tcl_provider(tmp_path):
    config = TclConfig(enabled=True, username="u", password="p")
    provider = TclProvider(config, _secrets(tmp_path), tmp_path)
    fake = FakeTclCloud()
    provider.cloud = fake  # type: ignore[assignment]
    await provider.start()

    devices = await provider.list_devices()
    assert len(devices) == 1  # breeva is filtered out in this release
    device = devices[0]
    assert device.kind is DeviceKind.AC
    assert device.name == "客厅空调"

    values = await provider.refresh(device)
    assert values[Capability.POWER] is True
    assert values[Capability.MODE] == Mode.COOL.value
    assert values[Capability.FAN_SPEED] == 50

    await provider.execute(device, Command(Capability.TARGET_TEMP, 25.4))
    assert fake.desired == [("ac1", {"targetTemperature": 25})]


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
