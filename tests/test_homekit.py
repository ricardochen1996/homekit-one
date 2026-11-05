from __future__ import annotations

import asyncio

from homekit_one.config import Config, FakeConfig, GatewayConfig
from homekit_one.core.manager import DeviceManager
from homekit_one.core.model import Capability
from homekit_one.homekit.bridge import HomeKitBridge, _private_ipv4
from homekit_one.providers.fake import FakeProvider


async def _make(tmp_path):
    config = Config(
        gateway=GatewayConfig(
            state_dir=tmp_path,
            homekit_port=51990,
            poll_intervals={"fake": 0.05, "mijia": 1, "treeow": 1},
            extra_switches="all",
        ),
        fake=FakeConfig(enabled=True),
    )
    manager = DeviceManager(config, [FakeProvider()])
    await manager.start()
    bridge = HomeKitBridge(config, manager)
    return manager, bridge


async def test_accessory_services(tmp_path):
    manager, bridge = await _make(tmp_path)
    try:
        hap = bridge.bridge.to_HAP()
        service_types = {service["type"] for acc in hap for service in acc["services"]}
        assert "BB" in service_types  # AirPurifier
        assert "BC" in service_types  # HeaterCooler
        assert "B7" in service_types  # Fanv2
        assert "47" in service_types  # Outlet
        assert "8D" in service_types  # AirQualitySensor
        assert "BA" in service_types  # FilterMaintenance
        assert "8A" in service_types  # TemperatureSensor
        assert "82" in service_types  # HumiditySensor
        assert "49" in service_types  # Switch (display)
        assert bridge.pairing_info()["accessories"] == 5
    finally:
        await bridge.stop()
        await manager.stop()


async def test_characteristic_follows_manager_state(tmp_path):
    manager, bridge = await _make(tmp_path)
    try:
        wrapper = bridge._wrappers["fake:purifier"][0]
        rotation = next(char for spec, char in wrapper._bound if spec.name == "RotationSpeed")
        assert rotation.value == 30
        await manager.set_capability("fake:purifier", Capability.FAN_SPEED, 70)
        assert rotation.value == 70
    finally:
        await bridge.stop()
        await manager.stop()


async def test_characteristic_setter_reaches_provider(tmp_path):
    manager, bridge = await _make(tmp_path)
    try:
        wrapper = bridge._wrappers["fake:purifier"][0]
        active = next(char for spec, char in wrapper._bound if spec.name == "Active")
        active.client_update_value(0)
        await asyncio.sleep(0.9)
        assert manager.get("fake:purifier").get(Capability.POWER) is False
    finally:
        await bridge.stop()
        await manager.stop()


def test_private_ipv4_detection():
    assert _private_ipv4("192.168.1.86") is True
    assert _private_ipv4("10.0.0.5") is True
    assert _private_ipv4("172.20.1.5") is True
    assert _private_ipv4("172.32.1.5") is False
    assert _private_ipv4("198.18.0.1") is False
    assert _private_ipv4("28.0.0.1") is False
    assert _private_ipv4("127.0.0.1") is False
    assert _private_ipv4("not-an-ip") is False


async def test_sensor_services_are_linked(tmp_path):
    manager, bridge = await _make(tmp_path)
    try:
        hap = bridge.bridge.to_HAP()
        purifier = next(
            acc
            for acc in hap
            if any(service["type"] == "BB" for service in acc["services"])
        )
        air_purifier = next(
            service
            for service in purifier["services"]
            if service["type"] == "BB"
        )
        assert air_purifier.get("linked")
        assert air_purifier.get("primary") is True
    finally:
        await bridge.stop()
        await manager.stop()


async def test_minimal_profile_keeps_only_native_services(tmp_path):
    config = Config(
        gateway=GatewayConfig(
            state_dir=tmp_path,
            homekit_port=51996,
            poll_intervals={"fake": 0.05},
            extra_switches="minimal",
        ),
        fake=FakeConfig(enabled=True),
    )
    manager = DeviceManager(config, [FakeProvider()])
    await manager.start()
    bridge = HomeKitBridge(config, manager)
    try:
        hap = bridge.bridge.to_HAP()
        service_types = {service["type"] for acc in hap for service in acc["services"]}
        assert "49" not in service_types  # no extra switches for non-light/thermostat
        assert bridge.pairing_info()["accessories"] == 4
    finally:
        await bridge.stop()
        await manager.stop()
