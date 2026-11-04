"""TCL split-AC shadow field mapping."""

from __future__ import annotations

from typing import Any

from homeone.core.model import Capability, Command, Mode

# Only the two split-AC families were validated in the first release. Other
# TCL categories have different shadow contracts and stay out of the allow-list
# rather than sending guesses to a real compressor.
AC_DEVICE_TYPES = {
    "split ac",
    "split ac fresh air",
}

# TCL's windSpeed is an enum, not a percentage. 0 means auto; the rest are
# ordered steps. Mapping auto to 50% is a compromise: HomeKit has no "auto
# fan" on HeaterCooler, and reporting 0 while the unit is running looks broken.
NATIVE_TO_PERCENT = {0: 50, 2: 20, 3: 35, 4: 50, 5: 70, 6: 90}
PERCENT_TO_NATIVE = ((20, 2), (40, 3), (60, 4), (80, 5), (100, 6))


def is_ac_device(device_type: str) -> bool:
    return device_type.strip().lower() in AC_DEVICE_TYPES


def decode_shadow(
    raw: dict[str, Any], work_modes: dict[str, int]
) -> dict[Capability, Any]:
    values: dict[Capability, Any] = {}
    if _valid_bool(raw.get("powerSwitch")):
        values[Capability.POWER] = _bool(raw["powerSwitch"])
    if "targetTemperature" in raw:
        target = _float(raw["targetTemperature"], 0.0)
        # -1 / 0 show up when the unit is off or the field is unsupported.
        if 0.0 < target <= 60.0:
            values[Capability.TARGET_TEMP] = _clamp_temp(target)
    if "currentTemperature" in raw:
        current = _float(raw["currentTemperature"], 24.0)
        if -20.0 <= current <= 60.0:
            values[Capability.CURRENT_TEMP] = current
    if "workMode" in raw:
        native = int(_float(raw["workMode"], -1))
        if 0 <= native <= 20:
            mode = _mode_from_native(native, work_modes)
            if mode is not None:
                values[Capability.MODE] = mode
    if "windSpeed" in raw:
        native = int(_float(raw["windSpeed"], -1))
        if native in NATIVE_TO_PERCENT:
            values[Capability.FAN_SPEED] = NATIVE_TO_PERCENT[native]
    elif "windSpeed7Gear" in raw:
        gear = int(_float(raw["windSpeed7Gear"], -1))
        percent = {0: 50, 1: 14, 2: 28, 3: 42, 4: 57, 5: 71, 6: 85, 7: 100}.get(gear)
        if percent is not None:
            values[Capability.FAN_SPEED] = percent
    if _valid_bool(raw.get("verticalSwitch")):
        values[Capability.SWING] = _bool(raw["verticalSwitch"])
    return values


def encode_command(command: Command, work_modes: dict[str, int]) -> dict[str, Any]:
    capability = command.capability
    value = command.value
    if capability is Capability.POWER:
        return {"powerSwitch": 1 if value else 0}
    if capability is Capability.MODE:
        native = _native_from_mode(str(value), work_modes)
        return {"workMode": native} if native is not None else {}
    if capability is Capability.TARGET_TEMP:
        return {"targetTemperature": int(round(_clamp_temp(value)))}
    if capability is Capability.FAN_SPEED:
        percent = max(0, min(100, int(value)))
        native = next((native for limit, native in PERCENT_TO_NATIVE if percent <= limit), 6)
        return {"windSpeed": native, "windSpeedAutoSwitch": 0}
    if capability is Capability.SWING:
        return {"verticalSwitch": 1 if value else 0}
    return {}


def _mode_from_native(native: int, work_modes: dict[str, int]) -> str | None:
    for name, value in work_modes.items():
        if int(value) == native:
            return name
    # Do not guess a mode for an unknown value; the next refresh will retry.
    return None


def _native_from_mode(mode: str, work_modes: dict[str, int]) -> int | None:
    if mode in work_modes:
        return int(work_modes[mode])
    aliases = {
        Mode.COOL.value: "cool",
        Mode.HEAT.value: "heat",
        Mode.DRY.value: "dry",
        Mode.FAN.value: "fan",
        Mode.AUTO.value: "auto",
    }
    alias = aliases.get(mode)
    return int(work_modes[alias]) if alias in work_modes else None


def _valid_bool(value: Any) -> bool:
    return value in (0, 1, True, False, "0", "1", "true", "false", "True", "False")


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_temp(value: Any) -> float:
    return round(max(16.0, min(30.0, _float(value, 24.0))), 1)
