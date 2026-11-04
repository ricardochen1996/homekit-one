"""TCL+ TV status/property mapping."""

from __future__ import annotations

import json
from typing import Any

from homeone.core.model import Capability, Command, clamp

TV_CATEGORIES = {"TV", "SMARTTV", "SMART TV", "TELEVISION"}


def is_tv(category: Any) -> bool:
    return str(category or "").strip().upper() in TV_CATEGORIES


def parse_sources(status: dict[str, Any]) -> list[str]:
    """Parse sourceList, which is a JSON string containing JSON strings."""
    raw = status.get("sourceList")
    decoded: Any = raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = []
    sources: list[str] = []
    if isinstance(decoded, list):
        for item in decoded:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError:
                    continue
            if isinstance(item, dict):
                source = item.get("source")
                if source:
                    sources.append(str(source))
    current = status.get("source")
    if current:
        sources.append(str(current))
    return list(dict.fromkeys(sources))


def decode_tv_status(status: dict[str, Any]) -> dict[Capability, Any]:
    values: dict[Capability, Any] = {}
    if "powerSwitch" in status:
        values[Capability.POWER] = _bool(status["powerSwitch"])
    if "volume" in status:
        try:
            values[Capability.VOLUME] = int(clamp(float(status["volume"]), 0, 100))
        except (TypeError, ValueError):
            pass
    if "mute" in status:
        values[Capability.MUTE] = _bool(status["mute"])
    source = status.get("source")
    if source:
        values[Capability.INPUT] = str(source)
    return values


def encode_tv_command(command: Command) -> dict[str, Any]:
    capability = command.capability
    value = command.value
    if capability is Capability.POWER:
        return {"powerSwitch": 1 if value else 0}
    if capability is Capability.VOLUME:
        try:
            volume = int(clamp(float(value), 0, 100))
        except (TypeError, ValueError):
            return {}
        return {"volume": volume}
    if capability is Capability.MUTE:
        return {"mute": 1 if value else 0}
    if capability is Capability.INPUT and value:
        return {"source": str(value)}
    return {}


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)
