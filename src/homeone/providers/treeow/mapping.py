"""Treeow air-purifier property mapping."""

from __future__ import annotations

from typing import Any

from homeone.core.model import Capability, Command, Mode

MODE_TO_NATIVE = {
    Mode.MANUAL.value: 0,
    Mode.AUTO.value: 1,
    Mode.COMFORT.value: 2,
    Mode.SLEEP.value: 3,
}
NATIVE_TO_MODE = {value: key for key, value in MODE_TO_NATIVE.items()}

# fan_speed_enum 0..6, matching the upstream integration's percentage table.
FAN_TO_PERCENT = (0, 14, 28, 42, 57, 71, 100)


def decode_props(props: dict[str, Any]) -> dict[Capability, Any]:
    values: dict[Capability, Any] = {}
    if "switch" in props:
        values[Capability.POWER] = _bool(props["switch"])
    if "mode" in props:
        native = int(_number(props["mode"], -1))
        if native in NATIVE_TO_MODE:
            values[Capability.MODE] = NATIVE_TO_MODE[native]
    if "fan_speed_enum" in props:
        speed = int(_number(props["fan_speed_enum"], -1))
        if 0 <= speed < len(FAN_TO_PERCENT):
            values[Capability.FAN_SPEED] = FAN_TO_PERCENT[speed]
    for key, capability in (
        ("pm25", Capability.PM25),
        ("temp_indoor", Capability.TEMPERATURE),
        ("humidity", Capability.HUMIDITY),
        ("filter_life", Capability.FILTER_LIFE),
    ):
        if key in props:
            values[capability] = _number(props[key], 0.0)
    if "child_lock" in props:
        values[Capability.CHILD_LOCK] = _bool(props["child_lock"])
    if "light" in props:
        values[Capability.DISPLAY] = _bool(props["light"])
    return values


def encode_command(command: Command) -> list[tuple[str, Any]]:
    """Return (property, value) pairs; the caller applies them in order."""
    capability = command.capability
    value = command.value
    if capability is Capability.POWER:
        return [("switch", bool(value))]
    if capability is Capability.MODE:
        native = MODE_TO_NATIVE.get(str(value))
        return [("mode", native)] if native is not None else []
    if capability is Capability.FAN_SPEED:
        percent = max(0, min(100, int(value)))
        if percent == 0:
            return [("mode", MODE_TO_NATIVE[Mode.SLEEP.value]), ("fan_speed_enum", 0)]
        nearest = min(
            range(1, len(FAN_TO_PERCENT)),
            key=lambda index: abs(FAN_TO_PERCENT[index] - percent),
        )
        return [("mode", MODE_TO_NATIVE[Mode.MANUAL.value]), ("fan_speed_enum", nearest)]
    if capability is Capability.CHILD_LOCK:
        return [("child_lock", bool(value))]
    if capability is Capability.DISPLAY:
        return [("light", bool(value))]
    return []


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def _number(value: Any, default: float) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return default
