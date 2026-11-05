"""MIoT-Spec-V2 -> provider-neutral capabilities.

The cloud device list gives a MIoT-Spec URN. That spec is enough to find the
common properties (on / brightness / mode / fan-level / sensors) without a
per-model table. Vendor-specific properties are ignored unless they match a
known capability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homekit_one.core.model import Capability, Command, DeviceKind, Mode, clamp

_LOGGER = logging.getLogger(__name__)

READ_CAPS = {
    Capability.CURRENT_TEMP,
    Capability.TEMPERATURE,
    Capability.HUMIDITY,
    Capability.PM25,
    Capability.FILTER_LIFE,
    Capability.OUTLET_IN_USE,
}

BOOL_CAPS = {
    Capability.CHILD_LOCK,
    Capability.SWING,
    Capability.MUTE,
    Capability.MOTION,
    Capability.MOTION_DETECTION,
    Capability.CAMERA_WDR,
    Capability.CAMERA_FULL_COLOR,
    Capability.CAMERA_TRACKING,
    Capability.CAMERA_WATERMARK,
    Capability.CARPET_BOOST,
    Capability.AUTO_DUST_ARREST,
    Capability.AUTO_MOP_DRY,
    Capability.NO_DISTURB,
    Capability.AI_CLEANING,
    Capability.SLEEP_MODE,
    Capability.MITE_REMOVAL,
    Capability.ALARM,
}

DEVICE_TYPES = {
    "light": DeviceKind.LIGHT,
    "outlet": DeviceKind.PLUG,
    "switch": DeviceKind.SWITCH,
    "air-purifier": DeviceKind.PURIFIER,
    "fan": DeviceKind.FAN,
    "air-conditioner": DeviceKind.AC,
    "thermostat": DeviceKind.THERMOSTAT,
    "electric-blanket": DeviceKind.THERMOSTAT,
    "heater": DeviceKind.THERMOSTAT,
    "sensor": DeviceKind.SENSOR,
    "camera": DeviceKind.CAMERA,
    "vacuum": DeviceKind.VACUUM,
    "pet-feeder": DeviceKind.PET_FEEDER,
    "pet-drinking-fountain": DeviceKind.PET_WATERER,
    "speaker": DeviceKind.SPEAKER,
}

SKIP_TYPES = {
    "gateway",
    "central-hub",
    "hub",
    "router",
    "curtain",
    "humidifier",
    "dehumidifier",
    "cooker",
    "kettle",
}


@dataclass(frozen=True)
class SpecProperty:
    siid: int
    piid: int
    name: str
    type: str
    format: str
    access: tuple[str, ...]
    value_list: tuple[dict[str, Any], ...] = ()
    value_range: tuple[float, float, float] | None = None
    unit: str | None = None
    description: str = ""

    @property
    def writable(self) -> bool:
        return "write" in self.access

    @property
    def key(self) -> tuple[int, int]:
        return (self.siid, self.piid)


@dataclass(frozen=True)
class SpecAction:
    """A MIoT action used when a capability has no writable property."""

    siid: int
    aiid: int
    in_piids: tuple[int, ...] = ()
    in_params: tuple[dict[str, Any], ...] = ()


@dataclass
class CloudSpec:
    urn: str
    device_type: str
    kind: DeviceKind
    props: dict[Capability, SpecProperty] = field(default_factory=dict)
    capabilities: set[Capability] = field(default_factory=set)
    writable: set[Capability] = field(default_factory=set)
    mode_to_native: dict[str, int] = field(default_factory=dict)
    native_to_mode: dict[int, str] = field(default_factory=dict)
    fan_values: tuple[float, ...] = ()
    color_temp_range: tuple[int, int] | None = None
    target_temp_range: tuple[float, float] | None = None
    actions: dict[bool, SpecAction] = field(default_factory=dict)
    action_commands: dict[Capability, SpecAction] = field(default_factory=dict)
    power_on_values: frozenset[int] | None = None
    power_on: Any = 1
    power_off: Any = 0

    def properties(self) -> list[tuple[int, int]]:
        return list(dict.fromkeys(prop.key for prop in self.props.values()))

    def decode(self, raw: dict[tuple[int, int], Any]) -> dict[Capability, Any]:
        values: dict[Capability, Any] = {}
        for capability, prop in self.props.items():
            if prop.key not in raw:
                continue
            value = raw[prop.key]
            if capability is Capability.POWER:
                if self.power_on_values is not None:
                    values[capability] = int(_number(value, 0)) in self.power_on_values
                else:
                    values[capability] = _bool(value)
            elif capability is Capability.MODE:
                native = int(_number(value, 0))
                values[capability] = self.native_to_mode.get(native, Mode.MANUAL.value)
            elif capability is Capability.FAN_SPEED:
                values[capability] = _fan_percent(
                    _number(value, 0), self.fan_values
                )
            elif capability in (
                Capability.TARGET_TEMP,
                Capability.CURRENT_TEMP,
                Capability.TEMPERATURE,
                Capability.HUMIDITY,
                Capability.PM25,
                Capability.FILTER_LIFE,
                Capability.BRIGHTNESS,
                Capability.COLOR_TEMP,
                Capability.VOLUME,
                Capability.BATTERY,
            ):
                values[capability] = _number(value, 0)
            elif capability in BOOL_CAPS:
                values[capability] = _bool(value)
        if Capability.OUTLET_IN_USE in self.capabilities:
            values[Capability.OUTLET_IN_USE] = values.get(
                Capability.POWER, False
            )
        return values

    def encode(self, command: Command) -> list[tuple[int, int, Any]]:
        capability = command.capability
        prop = self.props.get(capability)
        if prop is None or capability in READ_CAPS:
            return []
        if capability is Capability.POWER and self.actions:
            # Action-driven power (vacuum / feeder / speaker) is handled by
            # ``action_for``; never write the read-only status property.
            return []
        if capability not in self.writable and capability is not Capability.POWER:
            return []
        value = command.value
        if capability is Capability.POWER:
            native = self.power_on if bool(value) else self.power_off
            return [(*prop.key, _as_format(prop.format, native))]
        if capability is Capability.MODE:
            native = self.mode_to_native.get(str(value))
            return [(*prop.key, native)] if native is not None else []
        if capability is Capability.FAN_SPEED:
            percent = max(0, min(100, int(value)))
            if (
                percent == 0
                and self.kind in (DeviceKind.PURIFIER, DeviceKind.FAN)
                and Capability.POWER in self.writable
            ):
                power = self.props[Capability.POWER]
                return [(*power.key, _as_format(power.format, False))]
            native = _nearest_fan(percent, self.fan_values)
            if native is not None and float(native).is_integer():
                native = int(native)
            return [(*prop.key, native)] if native is not None else []
        if capability is Capability.TARGET_TEMP:
            return [(*prop.key, _temp_value(prop.format, _number(value, 24.0)))]
        if capability is Capability.BRIGHTNESS:
            return [(*prop.key, max(1, min(100, int(_number(value, 100)))))]
        if capability is Capability.COLOR_TEMP:
            low, high = self.color_temp_range or (2700, 6500)
            return [
                (
                    *prop.key,
                    int(clamp(_number(value, 4000), low, high)),
                )
            ]
        if capability in BOOL_CAPS:
            return [(*prop.key, _as_format(prop.format, bool(value)))]
        if capability is Capability.VOLUME:
            return [(*prop.key, max(0, min(100, int(_number(value, 0)))))]
        return []

    def action_for(self, command: Command) -> SpecAction | None:
        if command.capability is Capability.POWER and self.actions:
            return self.actions.get(bool(command.value))
        return self.action_commands.get(command.capability)


def build_cloud_spec(
    data: dict[str, Any], urn: str, model: str = ""
) -> CloudSpec | None:
    real_urn = str(data.get("type") or urn)
    device_type = _urn_part(real_urn, "device")
    props: dict[str, SpecProperty] = {}
    action_map: dict[str, SpecAction] = {}
    for service in data.get("services") or []:
        try:
            siid = int(service.get("iid", 0))
        except (TypeError, ValueError):
            continue
        for action in service.get("actions") or []:
            action_name = _urn_part(str(action.get("type") or ""), "action")
            try:
                aiid = int(action.get("iid", 0))
            except (TypeError, ValueError):
                continue
            if not action_name or not aiid:
                continue
            in_piids: list[int] = []
            for piid in action.get("in") or []:
                try:
                    in_piids.append(int(piid))
                except (TypeError, ValueError):
                    continue
            action_map[action_name] = SpecAction(
                siid=siid, aiid=aiid, in_piids=tuple(in_piids)
            )
        for item in service.get("properties") or []:
            try:
                piid = int(item.get("iid", 0))
            except (TypeError, ValueError):
                continue
            name = _property_name(str(item.get("type") or ""))
            if not name:
                continue
            spec_prop = SpecProperty(
                siid=siid,
                piid=piid,
                name=name,
                type=str(item.get("type") or ""),
                format=str(item.get("format") or ""),
                access=tuple(str(a).lower() for a in item.get("access") or []),
                value_list=tuple(item.get("value-list") or ()),
                value_range=_range(item.get("value-range")),
                unit=item.get("unit"),
                description=str(item.get("description") or ""),
            )
            # Keep the first standard MIoT property when a vendor service
            # repeats a name (e.g. speaker mute vs. microphone mute).
            existing = props.get(name)
            if existing is None or (
                "miot-spec-v2" not in existing.type
                and "miot-spec-v2" in spec_prop.type
            ):
                props[name] = spec_prop

    kind = DEVICE_TYPES.get(device_type)
    if device_type in SKIP_TYPES:
        return None
    if kind is None:
        kind = _infer_kind(props)
    if kind is None:
        return None

    spec = CloudSpec(urn=real_urn, device_type=device_type, kind=kind)
    _populate(spec, props, kind, action_map)
    if (
        kind not in (DeviceKind.SENSOR, DeviceKind.SPEAKER)
        and Capability.POWER not in spec.capabilities
    ):
        # Read-only cloud entries (battery sensors, meters, etc.) are still
        # useful as sensors, but must not get an Active setter in HomeKit.
        spec.kind = DeviceKind.SENSOR
        spec.props = {
            capability: prop
            for capability, prop in spec.props.items()
            if capability in READ_CAPS
        }
        spec.capabilities = set(spec.props)
        spec.writable.clear()
    if not spec.capabilities:
        return None
    if not spec.properties():
        return None
    return spec


def _populate(
    spec: CloudSpec,
    props: dict[str, SpecProperty],
    kind: DeviceKind,
    actions: dict[str, SpecAction],
) -> None:
    def add(
        capability: Capability,
        name: str,
        *,
        writable: bool = False,
        read_only: bool = False,
    ) -> SpecProperty | None:
        prop = props.get(name)
        if prop is None:
            return None
        if read_only:
            spec.props[capability] = prop
            spec.capabilities.add(capability)
            return prop
        if not prop.writable:
            return None
        spec.props[capability] = prop
        spec.capabilities.add(capability)
        if writable:
            spec.writable.add(capability)
        return prop

    if kind in (DeviceKind.LIGHT, DeviceKind.PLUG, DeviceKind.SWITCH):
        add(Capability.POWER, "on", writable=True)
        if spec.device_type == "dishwasher":
            start = actions.get("start-wash")
            stop = actions.get("stop-washing")
            if start is not None:
                spec.actions[True] = start
            if stop is not None:
                spec.actions[False] = stop
        if kind is DeviceKind.LIGHT:
            add(Capability.BRIGHTNESS, "brightness", writable=True)
            color = add(Capability.COLOR_TEMP, "color-temperature", writable=True)
            if color is not None and color.value_range:
                spec.color_temp_range = (
                    int(color.value_range[0]),
                    int(color.value_range[1]),
                )
        if kind is DeviceKind.PLUG:
            # Some plugs report temperature; expose it as a sensor service.
            add(Capability.TEMPERATURE, "temperature", read_only=True)
            add(Capability.CURRENT_TEMP, "current-temperature", read_only=True)
            if Capability.POWER in spec.capabilities:
                # OutletInUse mirrors the switch state; MIoT has no separate prop.
                spec.capabilities.add(Capability.OUTLET_IN_USE)
        return

    if kind is DeviceKind.SENSOR:
        add(Capability.TEMPERATURE, "temperature", read_only=True)
        add(Capability.CURRENT_TEMP, "current-temperature", read_only=True)
        add(Capability.HUMIDITY, "relative-humidity", read_only=True)
        add(Capability.PM25, "pm2.5-density", read_only=True)
        add(Capability.PM25, "pm25-density", read_only=True)
        return

    if kind is DeviceKind.THERMOSTAT:
        add(Capability.POWER, "on", writable=True)
        target = add(Capability.TARGET_TEMP, "target-temperature", writable=True)
        if target is not None and target.value_range:
            spec.target_temp_range = (
                float(target.value_range[0]),
                float(target.value_range[1]),
            )
        add(Capability.CURRENT_TEMP, "current-temperature", read_only=True)
        if Capability.CURRENT_TEMP not in spec.capabilities:
            add(Capability.CURRENT_TEMP, "temperature", read_only=True)
        add(Capability.CHILD_LOCK, "physical-controls-locked", writable=True)
        if spec.device_type == "electric-blanket":
            add(Capability.MITE_REMOVAL, "mode", writable=True)
            add(Capability.ALARM, "alarm", writable=True)
        return

    if kind is DeviceKind.CAMERA:
        add(Capability.POWER, "on", writable=True)
        add(Capability.MOTION, "motion-detection", read_only=True)
        add(Capability.MOTION_DETECTION, "motion-detection", writable=True)
        add(Capability.CAMERA_WDR, "wdr-mode", writable=True)
        add(Capability.CAMERA_FULL_COLOR, "glimmer-full-color", writable=True)
        add(Capability.CAMERA_TRACKING, "motion-tracking", writable=True)
        add(Capability.CAMERA_WATERMARK, "time-watermark", writable=True)
        return

    if kind is DeviceKind.SPEAKER:
        add(Capability.VOLUME, "volume", writable=True)
        add(Capability.MUTE, "mute", writable=True)
        add(Capability.SLEEP_MODE, "sleep-mode", writable=True)
        playing = props.get("playing-state")
        if playing is not None:
            spec.props[Capability.POWER] = playing
            spec.capabilities.add(Capability.POWER)
            spec.power_on_values = frozenset({1})
        if "play" in actions:
            spec.actions[True] = actions["play"]
        if "pause" in actions:
            spec.actions[False] = actions["pause"]
        return

    if kind is DeviceKind.VACUUM:
        status = props.get("status")
        if status is not None:
            spec.props[Capability.POWER] = status
            spec.capabilities.add(Capability.POWER)
            # Idle/charging/paused/error are off; anything actively working is on.
            spec.power_on_values = frozenset(
                {4, 6, 8, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21}
            )
        if "start-sweep" in actions:
            spec.actions[True] = actions["start-sweep"]
        stop = actions.get("stop-and-gocharge") or actions.get("stop-sweeping")
        if stop is not None:
            spec.actions[False] = stop
        add(Capability.BATTERY, "battery-level", read_only=True)
        add(Capability.FILTER_LIFE, "filter-life-level", read_only=True)
        add(Capability.CARPET_BOOST, "carpet-boost", writable=True)
        add(Capability.AUTO_DUST_ARREST, "auto-dust-arrest", writable=True)
        add(Capability.AUTO_MOP_DRY, "auto-mop-dry", writable=True)
        add(Capability.NO_DISTURB, "no-disturb", writable=True)
        add(Capability.AI_CLEANING, "ai-cleaning", writable=True)
        add(Capability.CHILD_LOCK, "physical-controls-locked", writable=True)
        add(Capability.ALARM, "alarm", writable=True)
        reset = actions.get("reset-filter-life")
        if reset is not None:
            spec.action_commands[Capability.FILTER_RESET] = reset
            spec.capabilities.add(Capability.FILTER_RESET)
        return

    if kind is DeviceKind.PET_FEEDER:
        add(Capability.CHILD_LOCK, "physical-controls-locked", writable=True)
        status = props.get("status")
        if status is not None:
            spec.props[Capability.POWER] = status
            spec.capabilities.add(Capability.POWER)
            spec.power_on_values = frozenset({1})  # Busy while dispensing
        feed = actions.get("pet-food-out")
        if feed is not None:
            spec.actions[True] = SpecAction(
                siid=feed.siid,
                aiid=feed.aiid,
                in_params=tuple(
                    {"piid": piid, "value": 1} for piid in feed.in_piids
                ),
            )
        return

    if kind is DeviceKind.PET_WATERER:
        mode = props.get("mode")
        if mode is not None:
            spec.props[Capability.POWER] = mode
            spec.capabilities.add(Capability.POWER)
            # Auto (0) is the normal idle mode; interval/constant mean the
            # pump is actively scheduled. The switch writes constant on and
            # auto off because the device has no hard power property.
            spec.power_on_values = frozenset({1, 2})
            spec.power_on = 2
            spec.power_off = 0
        add(Capability.FILTER_LIFE, "filter-life-level", read_only=True)
        add(Capability.BATTERY, "battery-level", read_only=True)
        add(Capability.CHILD_LOCK, "physical-controls-locked", writable=True)
        add(Capability.NO_DISTURB, "no-disturb", writable=True)
        reset = actions.get("reset-filter-life")
        if reset is not None:
            spec.action_commands[Capability.FILTER_RESET] = reset
            spec.capabilities.add(Capability.FILTER_RESET)
        return

    # Devices with a power switch plus environmental/control properties.
    add(Capability.POWER, "on", writable=True)
    mode = add(Capability.MODE, "mode", writable=True)
    if mode is not None:
        _build_mode_map(spec, mode)
        if not spec.mode_to_native:
            _drop(spec, Capability.MODE)
    fan = add(Capability.FAN_SPEED, "fan-level", writable=True)
    if fan is None:
        fan = add(Capability.FAN_SPEED, "fan-speed", writable=True)
    if fan is not None:
        spec.fan_values = _fan_values(fan)
        if not spec.fan_values:
            _drop(spec, Capability.FAN_SPEED)
    add(Capability.CHILD_LOCK, "physical-controls-locked", writable=True)
    add(Capability.SWING, "swing-mode", writable=True)
    add(Capability.SWING, "vertical-swing", writable=True)
    add(Capability.PM25, "pm2.5-density", read_only=True)
    add(Capability.PM25, "pm25-density", read_only=True)
    add(Capability.FILTER_LIFE, "filter-life-level", read_only=True)
    add(Capability.HUMIDITY, "relative-humidity", read_only=True)
    add(Capability.TEMPERATURE, "temperature", read_only=True)
    add(Capability.CURRENT_TEMP, "current-temperature", read_only=True)
    if kind is DeviceKind.AC:
        target = add(Capability.TARGET_TEMP, "target-temperature", writable=True)
        if target is not None and target.value_range:
            spec.target_temp_range = (
                float(target.value_range[0]),
                float(target.value_range[1]),
            )
        # ACs report room temperature as "temperature" on some models and
        # "current-temperature" on others; HomeKit's HeaterCooler wants
        # CurrentTemperature in both cases.
        if Capability.CURRENT_TEMP not in spec.capabilities and "temperature" in props:
            prop = props["temperature"]
            if prop.access and "read" in prop.access:
                spec.props[Capability.CURRENT_TEMP] = prop
                spec.capabilities.add(Capability.CURRENT_TEMP)
    if kind is DeviceKind.PURIFIER:
        # A purifier's "temperature" is the room sensor, not a target.
        if Capability.TEMPERATURE not in spec.capabilities and "temperature" in props:
            prop = props["temperature"]
            if "read" in prop.access:
                spec.props[Capability.TEMPERATURE] = prop
                spec.capabilities.add(Capability.TEMPERATURE)


def _drop(spec: CloudSpec, capability: Capability) -> None:
    spec.props.pop(capability, None)
    spec.capabilities.discard(capability)
    spec.writable.discard(capability)


def _infer_kind(props: dict[str, SpecProperty]) -> DeviceKind | None:
    has_on = "on" in props
    if has_on and "brightness" in props:
        return DeviceKind.LIGHT
    if has_on and ("fan-level" in props or "fan-speed" in props):
        if "pm2.5-density" in props or "pm25-density" in props or "filter-life-level" in props:
            return DeviceKind.PURIFIER
        return DeviceKind.FAN
    if has_on and "target-temperature" in props:
        return DeviceKind.AC
    if has_on:
        return DeviceKind.SWITCH
    if any(name in props for name in ("temperature", "relative-humidity", "pm2.5-density")):
        return DeviceKind.SENSOR
    return None


def _build_mode_map(spec: CloudSpec, prop: SpecProperty) -> None:
    for item in prop.value_list:
        try:
            native = int(item.get("value"))
        except (TypeError, ValueError):
            continue
        text = f"{item.get('description', '')} {item.get('comment', '')}".lower()
        mode = _mode_from_text(text)
        if mode is None:
            continue
        spec.native_to_mode[native] = mode
        spec.mode_to_native.setdefault(mode, native)


def _mode_from_text(text: str) -> str | None:
    if "auto" in text or "智能" in text:
        return Mode.AUTO.value
    if "cool" in text or "制冷" in text:
        return Mode.COOL.value
    if "heat" in text or "制热" in text:
        return Mode.HEAT.value
    if "dry" in text or "dehumid" in text or "除湿" in text:
        return Mode.DRY.value
    if "sleep" in text or "睡眠" in text:
        return Mode.SLEEP.value
    if "comfort" in text or "极净" in text:
        return Mode.COMFORT.value
    if "fan" in text or "送风" in text:
        return Mode.FAN.value
    if "manual" in text or "favorite" in text or "最爱" in text:
        return Mode.MANUAL.value
    if "smart" in text:
        return Mode.AUTO.value
    if "natural" in text or "straight" in text:
        return Mode.MANUAL.value
    return None


def _fan_values(prop: SpecProperty) -> tuple[float, ...]:
    values: list[float] = []
    for item in prop.value_list:
        try:
            values.append(float(item.get("value")))
        except (TypeError, ValueError):
            continue
    if values:
        return tuple(sorted(set(values)))
    if prop.value_range:
        low, high, step = prop.value_range
        step = step or 1
        if high - low > 100:
            return (low, high)
        count = int((high - low) / step) + 1
        if count <= 200:
            return tuple(low + step * index for index in range(count))
    return ()


def _fan_percent(native: float, values: tuple[float, ...]) -> int:
    if not values:
        return 0
    low, high = min(values), max(values)
    if high <= low:
        return 100
    # A fan level starting at 1 means "lowest", not "off". Map 1..4 to
    # 25/50/75/100; a 0-based level keeps 0 as off.
    if low >= 1:
        return int(round((native - low + 1) / (high - low + 1) * 100))
    return int(round((native - low) / (high - low) * 100))


def _nearest_fan(percent: int, values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    low, high = min(values), max(values)
    if low >= 1:
        target = low + percent / 100 * (high - low + 1) - 1
    else:
        target = low + percent / 100 * (high - low)
    return min(values, key=lambda value: (abs(value - target), -value))


def _property_name(type_urn: str) -> str:
    parts = type_urn.split(":")
    if "property" in parts:
        index = parts.index("property")
        if index + 1 < len(parts):
            return parts[index + 1]
    return parts[-1] if parts else ""


def _urn_part(urn: str, kind: str) -> str:
    parts = urn.split(":")
    if kind in parts:
        index = parts.index(kind)
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _range(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    try:
        low = float(value[0])
        high = float(value[1])
        step = float(value[2]) if len(value) > 2 else 1.0
    except (TypeError, ValueError):
        return None
    return (low, high, step or 1.0)


def _number(value: Any, default: float) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def _as_format(fmt: str, value: Any) -> Any:
    if fmt == "bool":
        return bool(value)
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def _temp_value(fmt: str, value: float) -> Any:
    return round(value, 1) if fmt in ("float", "double") else int(round(value))
