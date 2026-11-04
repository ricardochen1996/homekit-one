"""Provider-independent device model.

Every provider translates its native protocol into this small vocabulary, and
the HomeKit layer translates this vocabulary into HAP services. Keeping the
middle layer narrow is what makes adding a new brand tractable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Capability(StrEnum):
    POWER = "power"
    MODE = "mode"
    TARGET_TEMP = "target_temperature"
    CURRENT_TEMP = "current_temperature"
    FAN_SPEED = "fan_speed"  # 0-100, 0 means "not moving air"
    SWING = "swing"
    PM25 = "pm25"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    AIR_QUALITY = "air_quality"  # 0 unknown, 1 excellent .. 5 poor
    FILTER_LIFE = "filter_life"  # 0-100 percent
    CHILD_LOCK = "child_lock"
    DISPLAY = "display"
    BRIGHTNESS = "brightness"  # 0-100
    COLOR_TEMP = "color_temp"  # mired? no: Kelvin, provider-specific range
    OUTLET_IN_USE = "outlet_in_use"
    VOLUME = "volume"
    MUTE = "mute"
    INPUT = "input"


class DeviceKind(StrEnum):
    AC = "ac"
    PURIFIER = "purifier"
    FAN = "fan"
    PLUG = "plug"
    SWITCH = "switch"
    LIGHT = "light"
    TV = "tv"
    THERMOSTAT = "thermostat"
    DEHUMIDIFIER = "dehumidifier"
    SENSOR = "sensor"


class Mode(StrEnum):
    AUTO = "auto"
    COOL = "cool"
    HEAT = "heat"
    DRY = "dry"
    FAN = "fan"
    MANUAL = "manual"
    SLEEP = "sleep"
    COMFORT = "comfort"


READ_ONLY = {
    Capability.CURRENT_TEMP,
    Capability.PM25,
    Capability.TEMPERATURE,
    Capability.HUMIDITY,
    Capability.AIR_QUALITY,
    Capability.FILTER_LIFE,
    Capability.OUTLET_IN_USE,
}


@dataclass
class Command:
    capability: Capability
    value: Any


@dataclass
class Device:
    id: str
    name: str
    provider: str
    kind: DeviceKind
    model: str = ""
    manufacturer: str = ""
    capabilities: set[Capability] = field(default_factory=set)
    state: dict[Capability, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    online: bool = True
    error: str | None = None
    last_seen: float | None = None
    poll_interval: float | None = None

    @property
    def writable(self) -> set[Capability]:
        return self.capabilities - READ_ONLY

    def get(self, capability: Capability, default: Any = None) -> Any:
        return self.state.get(capability, default)

    def apply(self, values: dict[Capability, Any]) -> set[Capability]:
        changed: set[Capability] = set()
        for capability, value in values.items():
            if capability not in self.capabilities:
                continue
            if self.state.get(capability) != value:
                self.state[capability] = value
                changed.add(capability)
        return changed

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "kind": str(self.kind),
            "model": self.model,
            "manufacturer": self.manufacturer,
            "capabilities": sorted(str(c) for c in self.capabilities),
            "state": {str(k): v for k, v in sorted(self.state.items())},
            "online": self.online,
            "error": self.error,
            "last_seen": self.last_seen,
            "poll_interval": self.poll_interval,
        }


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
