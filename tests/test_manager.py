from __future__ import annotations

import asyncio
from typing import Any

import pytest

from homeone.config import Config, FakeConfig, GatewayConfig
from homeone.core.manager import DeviceManager
from homeone.core.model import Capability, Command, Device, DeviceKind
from homeone.providers.base import Provider
from homeone.providers.fake import FakeProvider


def _config(tmp_path) -> Config:
    return Config(
        gateway=GatewayConfig(
            state_dir=tmp_path,
            poll_intervals={"fake": 0.05, "mijia": 1, "tcl": 1, "treeow": 1},
        ),
        fake=FakeConfig(enabled=True),
    )


async def test_start_and_set_capability(tmp_path):
    config = _config(tmp_path)
    manager = DeviceManager(config, [FakeProvider()])
    await manager.start()
    try:
        assert len(manager.devices) == 4
        seen: list[tuple[str, set[Any]]] = []
        manager.add_listener(lambda device, changed: seen.append((device.id, changed)))

        await manager.set_capability("fake:purifier", Capability.FAN_SPEED, 60)
        assert manager.get("fake:purifier").get(Capability.FAN_SPEED) == 60
        assert seen
        await asyncio.sleep(0.1)
        assert manager.get("fake:purifier").online is True
    finally:
        await manager.stop()


async def test_poll_marks_device_offline(tmp_path):
    class BrokenProvider(Provider):
        name = "broken"

        async def list_devices(self) -> list[Device]:
            return [
                Device(
                    id="broken:1",
                    name="Broken",
                    provider=self.name,
                    kind=DeviceKind.SENSOR,
                    capabilities={Capability.TEMPERATURE},
                )
            ]

        async def refresh(self, device: Device) -> dict[Any, Any]:
            raise RuntimeError("offline")

        async def execute(self, device: Device, command: Command) -> None:
            raise RuntimeError("offline")

    config = _config(tmp_path)
    manager = DeviceManager(config, [BrokenProvider(None, None, tmp_path)])
    await manager.start()
    try:
        await asyncio.sleep(0.1)
        device = manager.get("broken:1")
        assert device is not None
        assert device.online is False
        assert "offline" in (device.error or "")
    finally:
        await manager.stop()


async def test_failed_command_rolls_back(tmp_path):
    class FailingProvider(Provider):
        name = "failing"

        async def list_devices(self) -> list[Device]:
            return [
                Device(
                    id="failing:1",
                    name="Failing",
                    provider=self.name,
                    kind=DeviceKind.PLUG,
                    capabilities={Capability.POWER},
                    state={Capability.POWER: True},
                )
            ]

        async def refresh(self, device: Device) -> dict[Any, Any]:
            return {Capability.POWER: True}

        async def execute(self, device: Device, command: Command) -> None:
            raise RuntimeError("cannot write")

    config = _config(tmp_path)
    manager = DeviceManager(config, [FailingProvider(None, None, tmp_path)])
    await manager.start()
    try:
        with pytest.raises(RuntimeError):
            await manager.set_capability("failing:1", Capability.POWER, False)
        assert manager.get("failing:1").get(Capability.POWER) is True
    finally:
        await manager.stop()
