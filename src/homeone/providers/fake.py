"""Demo provider used for first-run testing and UI development."""

from __future__ import annotations

import random
from typing import Any

from homeone.core.model import Capability, Command, Device, DeviceKind, Mode, clamp
from homeone.providers.base import Provider


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, config=None, secrets=None, state_dir=None) -> None:  # noqa: ANN001
        super().__init__(config, secrets, state_dir)
        self._devices: dict[str, Device] = {}
        self._build()

    def _build(self) -> None:
        self._devices = {
            "fake:ac": Device(
                id="fake:ac",
                name="演示空调",
                provider=self.name,
                kind=DeviceKind.AC,
                model="Fake-Split-AC",
                manufacturer="HomeOne",
                capabilities={
                    Capability.POWER,
                    Capability.MODE,
                    Capability.TARGET_TEMP,
                    Capability.CURRENT_TEMP,
                    Capability.FAN_SPEED,
                    Capability.SWING,
                },
                state={
                    Capability.POWER: False,
                    Capability.MODE: Mode.COOL.value,
                    Capability.TARGET_TEMP: 24.0,
                    Capability.CURRENT_TEMP: 27.5,
                    Capability.FAN_SPEED: 40,
                    Capability.SWING: False,
                },
            ),
            "fake:purifier": Device(
                id="fake:purifier",
                name="演示净化器",
                provider=self.name,
                kind=DeviceKind.PURIFIER,
                model="Fake-Purifier",
                manufacturer="HomeOne",
                capabilities={
                    Capability.POWER,
                    Capability.MODE,
                    Capability.FAN_SPEED,
                    Capability.PM25,
                    Capability.TEMPERATURE,
                    Capability.HUMIDITY,
                    Capability.AIR_QUALITY,
                    Capability.FILTER_LIFE,
                    Capability.CHILD_LOCK,
                    Capability.DISPLAY,
                },
                state={
                    Capability.POWER: True,
                    Capability.MODE: Mode.AUTO.value,
                    Capability.FAN_SPEED: 30,
                    Capability.PM25: 18.0,
                    Capability.TEMPERATURE: 23.4,
                    Capability.HUMIDITY: 52.0,
                    Capability.FILTER_LIFE: 76,
                    Capability.CHILD_LOCK: False,
                    Capability.DISPLAY: True,
                    Capability.AIR_QUALITY: 2,
                },
            ),
            "fake:fan": Device(
                id="fake:fan",
                name="演示循环扇",
                provider=self.name,
                kind=DeviceKind.FAN,
                model="Fake-Fan",
                manufacturer="HomeOne",
                capabilities={
                    Capability.POWER,
                    Capability.FAN_SPEED,
                    Capability.SWING,
                    Capability.TEMPERATURE,
                },
                state={
                    Capability.POWER: False,
                    Capability.FAN_SPEED: 0,
                    Capability.SWING: False,
                    Capability.TEMPERATURE: 24.8,
                },
            ),
            "fake:plug": Device(
                id="fake:plug",
                name="演示插座",
                provider=self.name,
                kind=DeviceKind.PLUG,
                model="Fake-Plug",
                manufacturer="HomeOne",
                capabilities={Capability.POWER, Capability.OUTLET_IN_USE},
                state={Capability.POWER: True, Capability.OUTLET_IN_USE: True},
            ),
        }

    async def list_devices(self) -> list[Device]:
        return list(self._devices.values())

    async def refresh(self, device: Device) -> dict[Capability, Any]:
        state = self._devices[device.id].state
        if device.kind is DeviceKind.AC:
            current = float(state.get(Capability.CURRENT_TEMP, 26.0))
            target = float(state.get(Capability.TARGET_TEMP, 24.0))
            on = bool(state.get(Capability.POWER))
            state[Capability.CURRENT_TEMP] = round(
                current + ((target - current) * 0.15 if on else 0.02), 1
            )
        if device.kind is DeviceKind.PURIFIER:
            pm25 = float(state.get(Capability.PM25, 20.0))
            fan = int(state.get(Capability.FAN_SPEED, 0))
            if state.get(Capability.POWER):
                pm25 = clamp(pm25 + random.uniform(-3, 3) - fan / 60.0, 2, 300)
            else:
                pm25 = clamp(pm25 + random.uniform(-1, 2), 2, 300)
            state[Capability.PM25] = round(pm25, 1)
            state[Capability.AIR_QUALITY] = _air_quality(pm25)
        if device.kind is DeviceKind.PLUG:
            state[Capability.OUTLET_IN_USE] = bool(state.get(Capability.POWER))
        return dict(state)

    async def execute(self, device: Device, command: Command) -> None:
        target = self._devices[device.id]
        target.state[command.capability] = command.value
        if command.capability is Capability.POWER and not command.value:
            if Capability.FAN_SPEED in target.state:
                target.state[Capability.FAN_SPEED] = 0
        if command.capability is Capability.FAN_SPEED and command.value:
            target.state[Capability.POWER] = True


def _air_quality(pm25: float) -> int:
    if pm25 <= 12:
        return 1
    if pm25 <= 35:
        return 2
    if pm25 <= 55:
        return 3
    if pm25 <= 150:
        return 4
    return 5
