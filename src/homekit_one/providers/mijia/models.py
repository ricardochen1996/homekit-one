"""Per-model MIoT property maps.

The MIoT spec is discoverable, but the siid/piid layout differs per model and
firmware. These four tables cover the first-release models; add a new
``ModelSpec`` (and, if needed, a spec file for reference) to support another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homekit_one.core.model import Capability, Command, DeviceKind, Mode

Prop = tuple[int, int]


@dataclass(frozen=True)
class ModelSpec:
    kind: DeviceKind
    manufacturer: str = "Xiaomi"
    power: Prop = (2, 1)
    mode: Prop | None = None
    mode_to_native: dict[str, int] = field(default_factory=dict)
    native_to_mode: dict[int, str] = field(default_factory=dict)
    fan: Prop | None = None
    fan_presets: tuple[int, ...] = ()
    fan_min: int = 1
    fan_max: int = 100
    fan_mode: str | None = None
    swing: Prop | None = None
    pm25: Prop | None = None
    temperature: Prop | None = None
    humidity: Prop | None = None
    filter_life: Prop | None = None
    child_lock: Prop | None = None
    display: Prop | None = None
    display_on: int = 1
    display_off: int = 0
    brightness: Prop | None = None
    color_temp: Prop | None = None
    color_temp_range: tuple[int, int] = (2700, 6500)
    extra_capabilities: frozenset[Capability] = frozenset()

    @property
    def capabilities(self) -> set[Capability]:
        caps: set[Capability] = {Capability.POWER}
        for prop, capability in (
            (self.mode, Capability.MODE),
            (self.fan, Capability.FAN_SPEED),
            (self.swing, Capability.SWING),
            (self.pm25, Capability.PM25),
            (self.temperature, Capability.TEMPERATURE),
            (self.humidity, Capability.HUMIDITY),
            (self.filter_life, Capability.FILTER_LIFE),
            (self.child_lock, Capability.CHILD_LOCK),
            (self.display, Capability.DISPLAY),
            (self.brightness, Capability.BRIGHTNESS),
            (self.color_temp, Capability.COLOR_TEMP),
        ):
            if prop is not None:
                caps.add(capability)
        caps |= set(self.extra_capabilities)
        return caps

    def properties(self) -> list[Prop]:
        props = [self.power]
        for prop in (
            self.mode,
            self.fan,
            self.swing,
            self.pm25,
            self.temperature,
            self.humidity,
            self.filter_life,
            self.child_lock,
            self.display,
            self.brightness,
            self.color_temp,
        ):
            if prop is not None and prop not in props:
                props.append(prop)
        return props

    def decode(self, raw: dict[Prop, Any]) -> dict[Capability, Any]:
        values: dict[Capability, Any] = {}
        if self.power in raw:
            values[Capability.POWER] = bool(raw[self.power])
        if self.mode and self.mode in raw:
            native = int(raw[self.mode])
            values[Capability.MODE] = self.native_to_mode.get(native, Mode.MANUAL.value)
        if self.fan and self.fan in raw:
            values[Capability.FAN_SPEED] = _fan_percent(
                int(raw[self.fan]), self.fan_presets, self.fan_min, self.fan_max
            )
        if self.swing and self.swing in raw:
            values[Capability.SWING] = bool(raw[self.swing])
        for prop, capability in (
            (self.pm25, Capability.PM25),
            (self.temperature, Capability.TEMPERATURE),
            (self.humidity, Capability.HUMIDITY),
            (self.filter_life, Capability.FILTER_LIFE),
            (self.brightness, Capability.BRIGHTNESS),
            (self.color_temp, Capability.COLOR_TEMP),
        ):
            if prop is not None and prop in raw:
                values[capability] = _number(raw[prop])
        if self.child_lock and self.child_lock in raw:
            values[Capability.CHILD_LOCK] = bool(raw[self.child_lock])
        if self.display and self.display in raw:
            values[Capability.DISPLAY] = int(raw[self.display]) != self.display_off
        if Capability.OUTLET_IN_USE in self.extra_capabilities:
            values[Capability.OUTLET_IN_USE] = values.get(
                Capability.POWER, bool(raw.get(self.power, False))
            )
        return values

    def encode(self, command: Command) -> list[tuple[int, int, Any]]:
        capability = command.capability
        value = command.value
        if capability is Capability.POWER:
            return [(*self.power, 1 if value else 0)]
        if capability is Capability.MODE and self.mode:
            native = self.mode_to_native.get(str(value))
            if native is None:
                return []
            return [(*self.mode, native)]
        if capability is Capability.FAN_SPEED and self.fan:
            percent = max(0, min(100, int(value)))
            if percent == 0:
                return [(*self.power, 0)]
            native = _fan_native(
                percent, self.fan_presets, self.fan_min, self.fan_max
            )
            writes: list[tuple[int, int, Any]] = [(*self.fan, native), (*self.power, 1)]
            if self.fan_mode and self.mode:
                mode_native = self.mode_to_native.get(self.fan_mode)
                if mode_native is not None:
                    writes.append((*self.mode, mode_native))
            return writes
        if capability is Capability.SWING and self.swing:
            return [(*self.swing, 1 if value else 0)]
        if capability is Capability.CHILD_LOCK and self.child_lock:
            return [(*self.child_lock, 1 if value else 0)]
        if capability is Capability.DISPLAY and self.display:
            return [(*self.display, self.display_on if value else self.display_off)]
        if capability is Capability.BRIGHTNESS and self.brightness:
            return [(*self.brightness, max(1, min(100, int(value))))]
        if capability is Capability.COLOR_TEMP and self.color_temp:
            low, high = self.color_temp_range
            kelvin = max(low, min(high, int(value)))
            return [(*self.color_temp, kelvin)]
        return []


def _number(value: Any) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def _fan_percent(native: int, presets: tuple[int, ...], low: int, high: int) -> int:
    if presets:
        top = max(presets)
        return int(round(max(0, native) / top * 100)) if top else 0
    span = max(1, high - low)
    return int(round(max(0, min(high, native) - low) / span * 100))


def _fan_native(percent: int, presets: tuple[int, ...], low: int, high: int) -> int:
    if presets:
        target = percent / 100 * max(presets)
        return min(presets, key=lambda preset: abs(preset - target))
    return int(round(low + percent / 100 * (high - low)))


MODELS: dict[str, ModelSpec] = {
    "zhimi.airpurifier.rma1": ModelSpec(
        kind=DeviceKind.PURIFIER,
        power=(2, 1),
        mode=(2, 4),
        mode_to_native={Mode.AUTO.value: 0, Mode.SLEEP.value: 1, Mode.MANUAL.value: 2},
        native_to_mode={0: Mode.AUTO.value, 1: Mode.SLEEP.value, 2: Mode.MANUAL.value},
        fan=(9, 2),
        fan_presets=(4, 9, 14),
        fan_mode=Mode.MANUAL.value,
        pm25=(3, 4),
        temperature=(3, 7),
        humidity=(3, 1),
        filter_life=(4, 1),
        child_lock=(8, 1),
        display=(13, 2),
        display_on=2,
        display_off=0,
    ),
    "dmaker.fan.p28": ModelSpec(
        kind=DeviceKind.FAN,
        power=(2, 1),
        mode=(2, 3),
        mode_to_native={Mode.AUTO.value: 2, Mode.MANUAL.value: 0, Mode.SLEEP.value: 3},
        native_to_mode={0: Mode.MANUAL.value, 1: Mode.MANUAL.value, 2: Mode.AUTO.value, 3: Mode.SLEEP.value},
        fan=(8, 1),
        fan_min=1,
        fan_max=100,
        swing=(2, 4),
        temperature=(9, 1),
        humidity=(9, 2),
        child_lock=(7, 1),
        display=(4, 1),
    ),
    "chuangmi.plug.m3": ModelSpec(
        kind=DeviceKind.PLUG,
        power=(2, 1),
        temperature=(2, 2),
        display=(3, 1),
        extra_capabilities=frozenset({Capability.OUTLET_IN_USE}),
    ),
    "yeelink.light.lamp4": ModelSpec(
        kind=DeviceKind.LIGHT,
        power=(2, 1),
        brightness=(2, 2),
        color_temp=(2, 3),
        color_temp_range=(2600, 5000),
    ),
}
