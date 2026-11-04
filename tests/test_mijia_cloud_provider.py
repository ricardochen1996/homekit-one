from __future__ import annotations

from typing import Any

from homeone.config import MijiaCloudConfig
from homeone.core.model import Capability, Command, DeviceKind
from homeone.providers.mijia_cloud.provider import MijiaCloudProvider
from homeone.secrets import SecretsStore
from tests.test_cloud_spec import prop, service, spec


class FakeCloud:
    def __init__(self) -> None:
        self.light = spec(
            "light",
            [
                service(
                    2,
                    "light",
                    [
                        prop(2, 1, "on"),
                        prop(2, 2, "brightness", "uint8", **{"value-range": [1, 100, 1]}),
                    ],
                )
            ],
        )
        self.purifier = spec(
            "air-purifier",
            [
                service(
                    2,
                    "air-purifier",
                    [
                        prop(2, 1, "on"),
                        prop(
                            2,
                            4,
                            "mode",
                            "uint8",
                            **{
                                "value-list": [
                                    {"value": 0, "description": "Auto"},
                                    {"value": 1, "description": "Sleep"},
                                ]
                            },
                        ),
                        prop(
                            2,
                            2,
                            "fan-level",
                            "uint8",
                            **{
                                "value-list": [
                                    {"value": 1, "description": "Level1"},
                                    {"value": 2, "description": "Level2"},
                                ]
                            },
                        ),
                    ],
                )
            ],
        )
        self.records = [
            {
                "did": "light1",
                "name": "客厅灯",
                "model": "test.light",
                "spec_type": self.light["type"],
                "isOnline": 1,
            },
            {
                "did": "pur1",
                "name": "净化器",
                "model": "test.purifier",
                "spec_type": self.purifier["type"],
                "isOnline": 1,
                "parent_id": "hub1",
            },
            {
                "did": "curtain1",
                "name": "窗帘",
                "model": "test.curtain",
                "spec_type": "urn:miot-spec-v2:device:curtain:0000A00C:test:1",
                "isOnline": 1,
            },
            {"did": "no-urn", "name": "without spec", "model": "test.x"},
        ]
        self.writes: list[dict[str, Any]] = []

    async def login(self) -> None:
        return None

    async def get_device_records(self):
        return self.records

    async def get_spec(self, urn: str):
        if urn == self.light["type"]:
            return self.light
        if urn == self.purifier["type"]:
            return self.purifier
        return spec("curtain", [service(2, "curtain", [prop(2, 1, "on")])])

    async def get_props(self, params):
        result = []
        for param in params:
            did = param["did"]
            key = (param["siid"], param["piid"])
            if did == "light1":
                values = {(2, 1): True, (2, 2): 50}
            else:
                values = {(2, 1): True, (2, 4): 0, (2, 2): 2}
            if key in values:
                result.append({**param, "value": values[key]})
        return result

    async def set_props(self, params):
        self.writes.extend(params)
        return [{"code": 0} for _ in params]

    async def close(self) -> None:
        return None


async def test_list_refresh_execute(tmp_path):
    config = MijiaCloudConfig(
        enabled=True, username="u", password="p", country="cn"
    )
    provider = MijiaCloudProvider(config, SecretsStore(tmp_path / "s.json"), tmp_path)
    fake = FakeCloud()
    provider.client = fake  # type: ignore[assignment]
    await provider.start()

    devices = await provider.list_devices()
    assert [device.id for device in devices] == [
        "mijia_cloud:light1",
        "mijia_cloud:pur1",
    ]
    light = devices[0]
    purifier = devices[1]
    assert light.kind is DeviceKind.LIGHT
    assert purifier.kind is DeviceKind.PURIFIER
    assert purifier.meta["parent_id"] == "hub1"

    values = await provider.refresh(light)
    assert values[Capability.POWER] is True
    assert values[Capability.BRIGHTNESS] == 50

    await provider.execute(light, Command(Capability.BRIGHTNESS, 80))
    assert fake.writes == [
        {"did": "light1", "siid": 2, "piid": 2, "value": 80}
    ]

    values = await provider.refresh(purifier)
    assert values[Capability.MODE] == "auto"
    assert values[Capability.FAN_SPEED] == 100


async def test_cloud_devices_reach_homekit(tmp_path):
    from homeone.config import Config, FakeConfig, GatewayConfig
    from homeone.core.manager import DeviceManager
    from homeone.homekit.bridge import HomeKitBridge

    config = Config(
        gateway=GatewayConfig(
            state_dir=tmp_path,
            homekit_port=51992,
            poll_intervals={"mijia_cloud": 0.05, "fake": 0.05},
        ),
        fake=FakeConfig(enabled=False),
        mijia_cloud=MijiaCloudConfig(enabled=True, username="u", password="p"),
    )
    provider = MijiaCloudProvider(
        config.mijia_cloud, SecretsStore(tmp_path / "s.json"), tmp_path
    )
    provider.client = FakeCloud()  # type: ignore[assignment]
    manager = DeviceManager(config, [provider])
    await manager.start()
    bridge = HomeKitBridge(config, manager)
    try:
        service_types = {
            service["type"]
            for accessory in bridge.bridge.to_HAP()
            for service in accessory["services"]
        }
        assert "43" in service_types  # Lightbulb
        assert "BB" in service_types  # AirPurifier
        assert bridge.pairing_info()["accessories"] >= 2
    finally:
        await bridge.stop()
        await manager.stop()


class FakeBlanketCloud(FakeCloud):
    def __init__(self) -> None:
        super().__init__()
        self.blanket = spec(
            "electric-blanket",
            [
                service(
                    2,
                    "electric-blanket",
                    [
                        prop(2, 5, "temperature", "int8", ("read", "notify")),
                        prop(
                            2,
                            4,
                            "target-temperature",
                            "uint8",
                            **{"value-range": [25, 45, 1]},
                        ),
                    ],
                ),
                service(
                    3,
                    "physical-controls-locked",
                    [prop(3, 1, "physical-controls-locked")],
                ),
                service(5, "switch", [prop(5, 2, "on")]),
            ],
        )
        self.records = [
            {
                "did": "blanket1",
                "name": "水暖毯",
                "model": "xiaomi.blanket.mj2",
                "spec_type": self.blanket["type"],
                "isOnline": 1,
            }
        ]

    async def get_spec(self, urn: str):
        return self.blanket

    async def get_props(self, params):
        values = {(5, 2): True, (2, 4): 32, (2, 5): 30, (3, 1): False}
        return [
            {**param, "value": values[(param["siid"], param["piid"])]}
            for param in params
            if (param["siid"], param["piid"]) in values
        ]


async def test_blanket_reaches_homekit_thermostat(tmp_path):
    from homeone.config import Config, FakeConfig, GatewayConfig
    from homeone.core.manager import DeviceManager
    from homeone.homekit.bridge import HomeKitBridge

    config = Config(
        gateway=GatewayConfig(
            state_dir=tmp_path,
            homekit_port=51991,
            poll_intervals={"mijia_cloud": 0.05, "fake": 0.05},
        ),
        fake=FakeConfig(enabled=False),
        mijia_cloud=MijiaCloudConfig(enabled=True, username="u", password="p"),
    )
    provider = MijiaCloudProvider(
        config.mijia_cloud, SecretsStore(tmp_path / "s.json"), tmp_path
    )
    provider.client = FakeBlanketCloud()  # type: ignore[assignment]
    manager = DeviceManager(config, [provider])
    await manager.start()
    bridge = HomeKitBridge(config, manager)
    try:
        parts = [
            service
            for accessory in bridge.bridge.to_HAP()
            for service in accessory["services"]
        ]
        service_types = {service["type"] for service in parts}
        assert "4A" in service_types  # Thermostat
        assert "49" in service_types  # child-lock Switch
    finally:
        await bridge.stop()
        await manager.stop()
