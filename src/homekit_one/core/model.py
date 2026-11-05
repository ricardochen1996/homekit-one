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
    BATTERY = "battery"  # 0-100 percent
    MOTION = "motion"
    VOC = "voc"  # formaldehyde / VOC reading
    UV = "uv"
    ANION = "anion"
    BUZZER = "buzzer"
    SMART_MODE = "smart_mode"
    SMART_NIGHT = "smart_night"
    FILTER_DAYS = "filter_days"
    FILTER_RESET = "filter_reset"
    MOTION_DETECTION = "motion_detection"
    CAMERA_WDR = "camera_wdr"
    CAMERA_FULL_COLOR = "camera_full_color"
    CAMERA_TRACKING = "camera_tracking"
    CAMERA_WATERMARK = "camera_watermark"
    CARPET_BOOST = "carpet_boost"
    AUTO_DUST_ARREST = "auto_dust_arrest"
    AUTO_MOP_DRY = "auto_mop_dry"
    NO_DISTURB = "no_disturb"
    AI_CLEANING = "ai_cleaning"
    SLEEP_MODE = "sleep_mode"
    MITE_REMOVAL = "mite_removal"
    ALARM = "alarm"


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
    VACUUM = "vacuum"
    CAMERA = "camera"
    PET_FEEDER = "pet_feeder"
    PET_WATERER = "pet_waterer"
    SPEAKER = "speaker"


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
    Capability.BATTERY,
    Capability.MOTION,
    Capability.VOC,
    Capability.FILTER_DAYS,
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

    @property
    def readings(self) -> dict[Capability, Any]:
        """Read-only values, aggregated for the web UI."""
        return {
            capability: value
            for capability, value in self.state.items()
            if capability not in self.writable
        }

    @property
    def controls(self) -> dict[Capability, Any]:
        """Writable values currently known, for the web UI."""
        return {
            capability: value
            for capability, value in self.state.items()
            if capability in self.writable
        }

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
