"""Translate the provider-neutral device model into HAP services.

HAP-python loads service definitions from Apple's published JSON. A service
comes with its required characteristics; optional ones are added explicitly.
Each optional characteristic is wired to a getter and, when writable, a setter
that schedules a manager command.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver

from homekit_one.core.manager import DeviceManager
from homekit_one.core.model import Capability, Command, Device, DeviceKind, Mode, clamp

_LOGGER = logging.getLogger(__name__)

Schedule = Callable[[Awaitable[Any]], None]

HEATER_COOLER = "HeaterCooler"
AIR_PURIFIER = "AirPurifier"
AIR_QUALITY_SENSOR = "AirQualitySensor"
TEMPERATURE_SENSOR = "TemperatureSensor"
HUMIDITY_SENSOR = "HumiditySensor"
FILTER_MAINTENANCE = "FilterMaintenance"
FAN_V2 = "Fanv2"
OUTLET = "Outlet"
LIGHTBULB = "Lightbulb"
SWITCH = "Switch"
VALVE = "Valve"
BATTERY_SERVICE = "BatteryService"
MOTION_SENSOR = "MotionSensor"

THERMOSTAT = "Thermostat"
TELEVISION = "Television"
TELEVISION_SPEAKER = "TelevisionSpeaker"
INPUT_SOURCE = "InputSource"

TARGET_AUTO = 0
TARGET_HEAT = 1
TARGET_COOL = 2

CURRENT_INACTIVE = 0
CURRENT_IDLE = 1
CURRENT_HEATING = 2
CURRENT_COOLING = 3

PURIFIER_INACTIVE = 0
PURIFIER_IDLE = 1
PURIFIER_PURIFYING = 2

FAN_INACTIVE = 0
FAN_IDLE = 1
FAN_BLOWING = 2

# Secondary services are linked to the primary service so the Home app can
# show them inside the accessory instead of as unrelated tiles.
LINKED_SERVICE_TYPES = {
    AIR_QUALITY_SENSOR,
    TEMPERATURE_SENSOR,
    HUMIDITY_SENSOR,
    FILTER_MAINTENANCE,
    BATTERY_SERVICE,
    MOTION_SENSOR,
    SWITCH,
    TELEVISION_SPEAKER,
    INPUT_SOURCE,
}

# With extra_switches="auto", only these user-facing extras get their own
# accessory. Everything else stays available via the web state/API and can be
# exposed with extra_switches="all".
AUTO_EXTRA_SWITCHES = {
    Capability.UV,
    Capability.ANION,
    Capability.DISPLAY,
    Capability.CAMERA_FULL_COLOR,
    Capability.CARPET_BOOST,
    Capability.MITE_REMOVAL,
}


@dataclass
class CharSpec:
    service: str
    name: str
    getter: Callable[[Device], Any]
    setter: Callable[[Any], Command | list[Command] | None] | None = None
    watch: set[Capability] = field(default_factory=set)
    properties: dict[str, Any] | None = None
    default: Any = None


class DeviceAccessory:
    """One HAP accessory plus the characteristic bindings that feed it."""

    def __init__(self, accessory: Accessory, device_id: str) -> None:
        self.accessory = accessory
        self.device_id = device_id
        self._bound: list[tuple[CharSpec, Any]] = []  # (spec, Characteristic)
        self._children: list[DeviceAccessory] = []

    def bind(self, driver: AccessoryDriver, specs: list[CharSpec], device: Device) -> None:
        grouped: dict[str, list[CharSpec]] = {}
        for spec in specs:
            grouped.setdefault(spec.service, []).append(spec)
        added: list[tuple[str, Any]] = []
        for service_key, service_specs in grouped.items():
            service_name = service_key.split("#", 1)[0]
            service = driver.loader.get_service(service_name)
            present = {char.display_name for char in service.characteristics}
            for spec in service_specs:
                if spec.name not in present:
                    service.add_characteristic(driver.loader.get_char(spec.name))
                    present.add(spec.name)
            for spec in service_specs:
                service.configure_char(
                    spec.name,
                    properties=spec.properties,
                    getter_callback=_getter(spec),
                    setter_callback=_setter(spec),
                )
            self.accessory.add_service(service)
            added.append((service_name, service))
            for spec in service_specs:
                self._bound.append((spec, service.get_characteristic(spec.name)))
        if added:
            primary = added[0][1]
            try:
                self.accessory.set_primary_service(primary)
            except AttributeError:
                pass
            for service_name, service in added[1:]:
                if service_name in LINKED_SERVICE_TYPES:
                    primary.add_linked_service(service)
        # Seed values so the first /accessories read is already correct.
        self.update(device, set())

    def update(self, device: Device | None, changed: set[Capability]) -> None:
        for spec, char in self._bound:
            if device is not None and changed and spec.watch and not (spec.watch & changed):
                continue
            try:
                value = spec.getter(device) if device is not None else _safe_default(spec)
                if value is None:
                    continue
                if char.value != value:
                    char.set_value(value, should_notify=True)
            except Exception:
                _LOGGER.exception("could not update %s", spec.name)
        for child in self._children:
            child.update(device, changed)

    def all_accessories(self) -> list[Accessory]:
        result = [self.accessory]
        for child in self._children:
            result.extend(child.all_accessories())
        return result


def _safe_default(spec: CharSpec) -> Any:
    return spec.default


def _getter(spec: CharSpec) -> Callable[[], Any]:
    def getter() -> Any:
        device = getattr(spec, "_device", None)
        if device is None:
            return spec.default
        value = spec.getter(device)
        return spec.default if value is None else value

    return getter


def _setter(spec: CharSpec) -> Callable[[Any], None] | None:
    if spec.setter is None:
        return None

    def setter(value: Any) -> None:
        callback = getattr(spec, "_schedule", None)
        manager = getattr(spec, "_manager", None)
        device = getattr(spec, "_device", None)
        if callback is None or manager is None or device is None:
            return
        result = spec.setter(value) if spec.setter else None
        if result is None:
            return
        commands = result if isinstance(result, list) else [result]

        async def apply() -> None:
            for command in commands:
                try:
                    await manager.set_capability(device.id, command.capability, command.value)
                except Exception as exc:
                    _LOGGER.warning("HomeKit command failed: %s", exc)
                    break

        callback(apply())

    return setter


def _bind_context(
    spec: CharSpec,
    manager: DeviceManager,
    device: Device,
    schedule: Schedule,
) -> CharSpec:
    # Small attribute-based context keeps the closures above readable; these
    # are per-build objects, never shared.
    spec._manager = manager  # type: ignore[attr-defined]
    spec._device = device  # type: ignore[attr-defined]
    spec._schedule = schedule  # type: ignore[attr-defined]
    return spec


# ---------------------------------------------------------------------------
# value helpers
# ---------------------------------------------------------------------------
def _power(device: Device) -> int:
    return 1 if device.get(Capability.POWER, False) else 0


def _power_command(value: Any) -> Command:
    return Command(Capability.POWER, bool(value))


def _target_temp(device: Device) -> float:
    return round(float(device.get(Capability.TARGET_TEMP, 24.0)), 1)


def _current_temp(device: Device) -> float:
    return round(float(device.get(Capability.CURRENT_TEMP, 20.0)), 1)


def _fan_speed(device: Device) -> float:
    value = device.get(Capability.FAN_SPEED, 0)
    return round(clamp(float(value), 0, 100), 1)


def _fan_command(value: Any) -> Command:
    return Command(Capability.FAN_SPEED, int(clamp(float(value), 0, 100)))


def _swing(device: Device) -> int:
    return 1 if device.get(Capability.SWING, False) else 0


def _swing_command(value: Any) -> Command:
    return Command(Capability.SWING, bool(value))


def _child_lock(device: Device) -> int:
    return 1 if device.get(Capability.CHILD_LOCK, False) else 0


def _child_lock_command(value: Any) -> Command:
    return Command(Capability.CHILD_LOCK, bool(value))


def _mode(device: Device) -> str:
    return str(device.get(Capability.MODE, Mode.AUTO.value))


def _target_heater_cooler_state(device: Device) -> int:
    mode = _mode(device)
    if mode == Mode.HEAT.value:
        return TARGET_HEAT
    if mode == Mode.AUTO.value:
        return TARGET_AUTO
    return TARGET_COOL


def _target_heater_cooler_command(value: Any) -> Command:
    state = int(value)
    mode = {
        TARGET_AUTO: Mode.AUTO.value,
        TARGET_HEAT: Mode.HEAT.value,
        TARGET_COOL: Mode.COOL.value,
    }.get(state, Mode.COOL.value)
    return Command(Capability.MODE, mode)


def _current_heater_cooler_state(device: Device) -> int:
    if not device.get(Capability.POWER, False):
        return CURRENT_INACTIVE
    mode = _mode(device)
    current = float(device.get(Capability.CURRENT_TEMP, 24.0))
    target = float(device.get(Capability.TARGET_TEMP, current))
    if mode == Mode.HEAT.value:
        return CURRENT_HEATING
    if mode == Mode.AUTO.value:
        if current < target - 0.5:
            return CURRENT_HEATING
        if current > target + 0.5:
            return CURRENT_COOLING
        return CURRENT_IDLE
    return CURRENT_COOLING


def _current_purifier_state(device: Device) -> int:
    if not device.get(Capability.POWER, False):
        return PURIFIER_INACTIVE
    return PURIFIER_PURIFYING if float(device.get(Capability.FAN_SPEED, 0)) > 0 else PURIFIER_IDLE


def _target_purifier_state(device: Device) -> int:
    return 1 if _mode(device) == Mode.AUTO.value else 0


def _target_purifier_command(value: Any) -> Command:
    return Command(
        Capability.MODE, Mode.AUTO.value if int(value) == 1 else Mode.MANUAL.value
    )


def _current_fan_state(device: Device) -> int:
    if not device.get(Capability.POWER, False):
        return FAN_INACTIVE
    return FAN_BLOWING if float(device.get(Capability.FAN_SPEED, 0)) > 0 else FAN_IDLE


def _target_fan_state(device: Device) -> int:
    return 1 if _mode(device) == Mode.AUTO.value else 0


def _target_fan_command(value: Any) -> Command:
    return Command(
        Capability.MODE, Mode.AUTO.value if int(value) == 1 else Mode.MANUAL.value
    )


def _air_quality(device: Device) -> int:
    explicit = device.get(Capability.AIR_QUALITY)
    if explicit is not None:
        return int(clamp(float(explicit), 0, 5))
    pm25 = float(device.get(Capability.PM25, 0))
    if pm25 <= 12:
        return 1
    if pm25 <= 35:
        return 2
    if pm25 <= 55:
        return 3
    if pm25 <= 150:
        return 4
    return 5


def _pm25(device: Device) -> float:
    return round(float(device.get(Capability.PM25, 0.0)), 1)


def _voc(device: Device) -> float:
    return round(float(device.get(Capability.VOC, 0.0)), 1)


def _filter_life(device: Device) -> float:
    return round(clamp(float(device.get(Capability.FILTER_LIFE, 100)), 0, 100), 1)


def _filter_change(device: Device) -> int:
    return 1 if _filter_life(device) <= 10 else 0


def _humidity(device: Device) -> float:
    return round(clamp(float(device.get(Capability.HUMIDITY, 50.0)), 0, 100), 1)


def _temperature(device: Device) -> float:
    value = device.get(Capability.TEMPERATURE)
    if value is None:
        value = device.get(Capability.CURRENT_TEMP, 20.0)
    return round(float(value), 1)


def _brightness(device: Device) -> int:
    return int(clamp(float(device.get(Capability.BRIGHTNESS, 100)), 0, 100))


def _brightness_command(value: Any) -> Command:
    return Command(Capability.BRIGHTNESS, int(clamp(float(value), 0, 100)))


def _kelvin_to_mired(device: Device, value: Any) -> int:
    kelvin = clamp(float(value), 1000, 10000)
    return int(clamp(round(1_000_000 / kelvin), 140, 400))


def _color_temp(device: Device) -> int:
    return _kelvin_to_mired(device, device.get(Capability.COLOR_TEMP, 4000))


def _color_temp_command(device: Device, value: Any) -> Command:
    mired = clamp(float(value), 140, 400)
    kelvin = int(round(1_000_000 / mired))
    return Command(Capability.COLOR_TEMP, kelvin)


def _battery_level(device: Device) -> int:
    return int(clamp(float(device.get(Capability.BATTERY, 100)), 0, 100))


def _battery_low(device: Device) -> int:
    return 1 if _battery_level(device) <= 20 else 0


def _motion(device: Device) -> int:
    return 1 if device.get(Capability.MOTION, False) else 0


def _battery_specs(device: Device) -> list[CharSpec]:
    return [
        CharSpec(BATTERY_SERVICE, "BatteryLevel", _battery_level, None, {Capability.BATTERY}),
        CharSpec(BATTERY_SERVICE, "StatusLowBattery", _battery_low, None, {Capability.BATTERY}),
        CharSpec(BATTERY_SERVICE, "ChargingState", lambda d: 0, None, set()),
    ]


def _filter_specs(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec(FILTER_MAINTENANCE, "FilterLifeLevel", _filter_life, None, {Capability.FILTER_LIFE}),
        CharSpec(
            FILTER_MAINTENANCE,
            "FilterChangeIndication",
            _filter_change,
            None,
            {Capability.FILTER_LIFE},
        ),
    ]
    if Capability.FILTER_RESET in device.capabilities:
        specs.append(
            CharSpec(
                FILTER_MAINTENANCE,
                "ResetFilterIndication",
                lambda d: 0,
                lambda value: Command(Capability.FILTER_RESET, True)
                if int(value)
                else None,
                {Capability.FILTER_RESET},
            )
        )
    return specs


def _specs_for_vacuum(device: Device) -> list[CharSpec]:
    specs = _specs_for_switch(device)
    if Capability.BATTERY in device.capabilities:
        specs.extend(_battery_specs(device))
    if Capability.FILTER_LIFE in device.capabilities:
        specs.extend(_filter_specs(device))
    return specs


def _specs_for_camera(device: Device) -> list[CharSpec]:
    specs = _specs_for_switch(device)
    if Capability.MOTION in device.capabilities:
        specs.append(
            CharSpec(MOTION_SENSOR, "MotionDetected", _motion, None, {Capability.MOTION})
        )
    return specs


def _specs_for_feeder(device: Device) -> list[CharSpec]:
    return _specs_for_switch(device)


def _specs_for_waterer(device: Device) -> list[CharSpec]:
    # A pet fountain is semantically a water valve, not a generic switch.
    specs = [
        CharSpec(VALVE, "Active", _power, _power_command, {Capability.POWER}),
        CharSpec(VALVE, "InUse", _power, None, {Capability.POWER}),
        CharSpec(VALVE, "ValveType", lambda d: 3, None, set()),  # Water faucet
    ]
    if Capability.BATTERY in device.capabilities:
        specs.extend(_battery_specs(device))
    if Capability.FILTER_LIFE in device.capabilities:
        specs.extend(_filter_specs(device))
    return specs


def _specs_for_speaker(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec("Speaker", "Mute", _tv_mute, _tv_mute_command, {Capability.MUTE}),
        CharSpec("Speaker", "Volume", _tv_volume, _tv_volume_command, {Capability.VOLUME}),
    ]
    if Capability.POWER in device.capabilities:
        specs.extend(_specs_for_switch(device))
    return specs


# ---------------------------------------------------------------------------
# accessory builders
# ---------------------------------------------------------------------------
def _specs_for_ac(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec(HEATER_COOLER, "Active", _power, _power_command, {Capability.POWER}),
        CharSpec(
            HEATER_COOLER,
            "CurrentHeaterCoolerState",
            _current_heater_cooler_state,
            None,
            {Capability.POWER, Capability.MODE, Capability.CURRENT_TEMP, Capability.TARGET_TEMP},
        ),
        CharSpec(
            HEATER_COOLER,
            "TargetHeaterCoolerState",
            _target_heater_cooler_state,
            _target_heater_cooler_command,
            {Capability.MODE},
        ),
        CharSpec(
            HEATER_COOLER,
            "CurrentTemperature",
            _current_temp,
            None,
            {Capability.CURRENT_TEMP},
        ),
        CharSpec(
            HEATER_COOLER,
            "HeatingThresholdTemperature",
            _target_temp,
            lambda value: Command(Capability.TARGET_TEMP, round(float(value), 1)),
            {Capability.TARGET_TEMP},
            {"minValue": 16, "maxValue": 30, "minStep": 1},
        ),
        CharSpec(
            HEATER_COOLER,
            "CoolingThresholdTemperature",
            _target_temp,
            lambda value: Command(Capability.TARGET_TEMP, round(float(value), 1)),
            {Capability.TARGET_TEMP},
            {"minValue": 16, "maxValue": 30, "minStep": 1},
        ),
        CharSpec(
            HEATER_COOLER,
            "RotationSpeed",
            _fan_speed,
            _fan_command,
            {Capability.FAN_SPEED},
        ),
        CharSpec(
            HEATER_COOLER,
            "SwingMode",
            _swing,
            _swing_command,
            {Capability.SWING},
        ),
        CharSpec(
            HEATER_COOLER,
            "TemperatureDisplayUnits",
            lambda device: 0,
            None,
            set(),
        ),
    ]
    return specs


def _specs_for_purifier(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec(AIR_PURIFIER, "Active", _power, _power_command, {Capability.POWER}),
        CharSpec(
            AIR_PURIFIER,
            "CurrentAirPurifierState",
            _current_purifier_state,
            None,
            {Capability.POWER, Capability.FAN_SPEED},
        ),
        CharSpec(
            AIR_PURIFIER,
            "TargetAirPurifierState",
            _target_purifier_state,
            _target_purifier_command,
            {Capability.MODE},
        ),
    ]
    if Capability.FAN_SPEED in device.capabilities:
        specs.append(
            CharSpec(
                AIR_PURIFIER,
                "RotationSpeed",
                _fan_speed,
                _fan_command,
                {Capability.FAN_SPEED},
            )
        )
    if Capability.SWING in device.capabilities:
        specs.append(
            CharSpec(AIR_PURIFIER, "SwingMode", _swing, _swing_command, {Capability.SWING})
        )
    if Capability.CHILD_LOCK in device.capabilities:
        specs.append(
            CharSpec(
                AIR_PURIFIER,
                "LockPhysicalControls",
                _child_lock,
                _child_lock_command,
                {Capability.CHILD_LOCK},
            )
        )

    if Capability.PM25 in device.capabilities or Capability.AIR_QUALITY in device.capabilities:
        specs.append(
            CharSpec(
                AIR_QUALITY_SENSOR,
                "AirQuality",
                _air_quality,
                None,
                {Capability.PM25, Capability.AIR_QUALITY},
            )
        )
    if Capability.PM25 in device.capabilities:
        specs.append(
            CharSpec(
                AIR_QUALITY_SENSOR,
                "PM2.5Density",
                _pm25,
                None,
                {Capability.PM25},
            )
        )
    if Capability.VOC in device.capabilities:
        specs.append(
            CharSpec(
                AIR_QUALITY_SENSOR,
                "VOCDensity",
                _voc,
                None,
                {Capability.VOC},
            )
        )
    if (
        Capability.TEMPERATURE in device.capabilities
        or Capability.CURRENT_TEMP in device.capabilities
    ):
        specs.append(
            CharSpec(
                TEMPERATURE_SENSOR,
                "CurrentTemperature",
                _temperature,
                None,
                {Capability.TEMPERATURE, Capability.CURRENT_TEMP},
            )
        )
    if Capability.HUMIDITY in device.capabilities:
        specs.append(
            CharSpec(
                HUMIDITY_SENSOR,
                "CurrentRelativeHumidity",
                _humidity,
                None,
                {Capability.HUMIDITY},
            )
        )
    if Capability.FILTER_LIFE in device.capabilities:
        specs.append(
            CharSpec(
                FILTER_MAINTENANCE,
                "FilterLifeLevel",
                _filter_life,
                None,
                {Capability.FILTER_LIFE},
            )
        )
        specs.append(
            CharSpec(
                FILTER_MAINTENANCE,
                "FilterChangeIndication",
                _filter_change,
                None,
                {Capability.FILTER_LIFE},
            )
        )
    if Capability.FILTER_RESET in device.capabilities:
        specs.append(
            CharSpec(
                FILTER_MAINTENANCE,
                "ResetFilterIndication",
                lambda d: 0,
                lambda value: Command(Capability.FILTER_RESET, True)
                if int(value)
                else None,
                {Capability.FILTER_RESET},
            )
        )
    return specs


def _specs_for_fan(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec(FAN_V2, "Active", _power, _power_command, {Capability.POWER}),
        CharSpec(
            FAN_V2,
            "CurrentFanState",
            _current_fan_state,
            None,
            {Capability.POWER, Capability.FAN_SPEED},
        ),
        CharSpec(
            FAN_V2,
            "RotationSpeed",
            _fan_speed,
            _fan_command,
            {Capability.FAN_SPEED},
        ),
    ]
    if Capability.MODE in device.capabilities:
        specs.append(
            CharSpec(
                FAN_V2,
                "TargetFanState",
                _target_fan_state,
                _target_fan_command,
                {Capability.MODE},
            )
        )
    if Capability.SWING in device.capabilities:
        specs.append(CharSpec(FAN_V2, "SwingMode", _swing, _swing_command, {Capability.SWING}))
    if (
        Capability.TEMPERATURE in device.capabilities
        or Capability.CURRENT_TEMP in device.capabilities
    ):
        specs.append(
            CharSpec(
                TEMPERATURE_SENSOR,
                "CurrentTemperature",
                _temperature,
                None,
                {Capability.TEMPERATURE, Capability.CURRENT_TEMP},
            )
        )
    if Capability.HUMIDITY in device.capabilities:
        specs.append(
            CharSpec(
                HUMIDITY_SENSOR,
                "CurrentRelativeHumidity",
                _humidity,
                None,
                {Capability.HUMIDITY},
            )
        )
    return specs


def _specs_for_plug(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec(OUTLET, "On", lambda d: bool(d.get(Capability.POWER, False)), _power_command, {Capability.POWER}),
        CharSpec(
            OUTLET,
            "OutletInUse",
            lambda d: bool(d.get(Capability.OUTLET_IN_USE, d.get(Capability.POWER, False))),
            None,
            {Capability.OUTLET_IN_USE, Capability.POWER},
        ),
    ]
    if (
        Capability.TEMPERATURE in device.capabilities
        or Capability.CURRENT_TEMP in device.capabilities
    ):
        specs.append(
            CharSpec(
                TEMPERATURE_SENSOR,
                "CurrentTemperature",
                _temperature,
                None,
                {Capability.TEMPERATURE, Capability.CURRENT_TEMP},
            )
        )
    return specs


def _tv_sources(device: Device) -> list[str]:
    sources = device.meta.get("sources") or []
    return [str(source) for source in sources if str(source)] or ["TV"]


def _tv_source_id(device: Device, source: Any) -> int:
    try:
        return _tv_sources(device).index(str(source)) + 1
    except ValueError:
        return 0


def _tv_source_name(device: Device, identifier: Any) -> str | None:
    try:
        index = int(identifier) - 1
    except (TypeError, ValueError):
        return None
    sources = _tv_sources(device)
    return sources[index] if 0 <= index < len(sources) else None


def _tv_active_identifier(device: Device) -> int:
    return _tv_source_id(device, device.get(Capability.INPUT, ""))


def _tv_input_command(device: Device, identifier: Any) -> Command | None:
    source = _tv_source_name(device, identifier)
    return Command(Capability.INPUT, source) if source else None


def _tv_volume(device: Device) -> int:
    return int(clamp(float(device.get(Capability.VOLUME, 0)), 0, 100))


def _tv_volume_command(value: Any) -> Command:
    return Command(Capability.VOLUME, int(clamp(float(value), 0, 100)))


def _tv_mute(device: Device) -> bool:
    return bool(device.get(Capability.MUTE, False))


def _tv_mute_command(value: Any) -> Command:
    return Command(Capability.MUTE, bool(value))


def _tv_input_type(source: str) -> int:
    name = source.upper()
    if name.startswith("HDMI"):
        return 3
    if name in ("TV", "TUNER", "DTV", "ATV"):
        return 2
    if name.startswith("AV") or "COMPOSITE" in name:
        return 4
    if name.startswith("USB"):
        return 9
    return 0


def _specs_for_tv(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec(TELEVISION, "Active", _power, _power_command, {Capability.POWER}),
        CharSpec(
            TELEVISION,
            "ActiveIdentifier",
            _tv_active_identifier,
            lambda value, d=device: _tv_input_command(d, value),
            {Capability.INPUT},
        ),
        CharSpec(
            TELEVISION,
            "ConfiguredName",
            lambda d: d.name,
            None,
            set(),
        ),
        CharSpec(
            TELEVISION,
            "SleepDiscoveryMode",
            lambda d: 1,
            None,
            set(),
        ),
        CharSpec(
            TELEVISION_SPEAKER,
            "Mute",
            _tv_mute,
            _tv_mute_command,
            {Capability.MUTE},
        ),
        CharSpec(
            TELEVISION_SPEAKER,
            "Volume",
            _tv_volume,
            _tv_volume_command,
            {Capability.VOLUME},
        ),
        CharSpec(
            TELEVISION_SPEAKER,
            "VolumeControlType",
            lambda d: 3,  # Absolute
            None,
            set(),
        ),
    ]
    for index, source in enumerate(_tv_sources(device), start=1):
        key = f"{INPUT_SOURCE}#{index}"
        specs.extend(
            [
                CharSpec(key, "Identifier", lambda d, i=index: i, None, set()),
                CharSpec(key, "ConfiguredName", lambda d, s=source: s, None, set()),
                CharSpec(
                    key,
                    "InputSourceType",
                    lambda d, s=source: _tv_input_type(s),
                    None,
                    set(),
                ),
                CharSpec(key, "IsConfigured", lambda d: 1, None, set()),
                CharSpec(key, "CurrentVisibilityState", lambda d: 0, None, set()),
            ]
        )
    return specs


def _target_heating_state(device: Device) -> int:
    return 1 if device.get(Capability.POWER, False) else 0


def _target_heating_command(value: Any) -> Command:
    return Command(Capability.POWER, int(value) != 0)


def _current_heating_state(device: Device) -> int:
    if not device.get(Capability.POWER, False):
        return 0
    return 2  # Heat; Thermostat has no separate idle value.


def _specs_for_thermostat(device: Device) -> list[CharSpec]:
    low, high = device.meta.get("target_temp_range") or (5, 45)
    return [
        CharSpec(
            THERMOSTAT,
            "CurrentTemperature",
            _current_temp,
            None,
            {Capability.CURRENT_TEMP, Capability.TEMPERATURE},
        ),
        CharSpec(
            THERMOSTAT,
            "TargetTemperature",
            _target_temp,
            lambda value: Command(Capability.TARGET_TEMP, round(float(value), 1)),
            {Capability.TARGET_TEMP},
            {"minValue": float(low), "maxValue": float(high), "minStep": 0.5},
        ),
        CharSpec(
            THERMOSTAT,
            "TargetHeatingCoolingState",
            _target_heating_state,
            _target_heating_command,
            {Capability.POWER},
        ),
        CharSpec(
            THERMOSTAT,
            "CurrentHeatingCoolingState",
            _current_heating_state,
            None,
            {Capability.POWER},
        ),
        CharSpec(
            THERMOSTAT,
            "TemperatureDisplayUnits",
            lambda device: 0,
            None,
            set(),
        ),
    ]


def _specs_for_switch(device: Device) -> list[CharSpec]:
    return [
        CharSpec(
            SWITCH,
            "On",
            lambda d: bool(d.get(Capability.POWER, False)),
            _power_command,
            {Capability.POWER},
        )
    ]


def _specs_for_light(device: Device) -> list[CharSpec]:
    specs = [
        CharSpec(LIGHTBULB, "On", lambda d: bool(d.get(Capability.POWER, False)), _power_command, {Capability.POWER}),
    ]
    if Capability.BRIGHTNESS in device.capabilities:
        specs.append(
            CharSpec(
                LIGHTBULB,
                "Brightness",
                _brightness,
                _brightness_command,
                {Capability.BRIGHTNESS},
            )
        )
    if Capability.COLOR_TEMP in device.capabilities:
        specs.append(
            CharSpec(
                LIGHTBULB,
                "ColorTemperature",
                _color_temp,
                lambda value, d=device: _color_temp_command(d, value),
                {Capability.COLOR_TEMP},
                {"minValue": 140, "maxValue": 400, "minStep": 1},
            )
        )
    return specs


def _specs_for_sensor(device: Device) -> list[CharSpec]:
    specs: list[CharSpec] = []
    if (
        Capability.TEMPERATURE in device.capabilities
        or Capability.CURRENT_TEMP in device.capabilities
    ):
        specs.append(
            CharSpec(
                TEMPERATURE_SENSOR,
                "CurrentTemperature",
                _temperature,
                None,
                {Capability.TEMPERATURE, Capability.CURRENT_TEMP},
            )
        )
    if Capability.HUMIDITY in device.capabilities:
        specs.append(
            CharSpec(
                HUMIDITY_SENSOR,
                "CurrentRelativeHumidity",
                _humidity,
                None,
                {Capability.HUMIDITY},
            )
        )
    if Capability.PM25 in device.capabilities:
        specs.append(
            CharSpec(
                AIR_QUALITY_SENSOR,
                "AirQuality",
                _air_quality,
                None,
                {Capability.PM25},
            )
        )
        specs.append(
            CharSpec(
                AIR_QUALITY_SENSOR,
                "PM2.5Density",
                _pm25,
                None,
                {Capability.PM25},
            )
        )
    if Capability.VOC in device.capabilities:
        specs.append(
            CharSpec(
                AIR_QUALITY_SENSOR,
                "VOCDensity",
                _voc,
                None,
                {Capability.VOC},
            )
        )
    return specs


SPEC_BUILDERS = {
    DeviceKind.AC: _specs_for_ac,
    DeviceKind.PURIFIER: _specs_for_purifier,
    DeviceKind.FAN: _specs_for_fan,
    DeviceKind.PLUG: _specs_for_plug,
    DeviceKind.SWITCH: _specs_for_switch,
    DeviceKind.LIGHT: _specs_for_light,
    DeviceKind.TV: _specs_for_tv,
    DeviceKind.THERMOSTAT: _specs_for_thermostat,
    DeviceKind.SENSOR: _specs_for_sensor,
    DeviceKind.VACUUM: _specs_for_vacuum,
    DeviceKind.CAMERA: _specs_for_camera,
    DeviceKind.PET_FEEDER: _specs_for_feeder,
    DeviceKind.PET_WATERER: _specs_for_waterer,
    DeviceKind.SPEAKER: _specs_for_speaker,
}


def _configure_info(accessory: Accessory, device: Device, firmware: str) -> None:
    info = accessory.get_service("AccessoryInformation")
    if info is None:
        return
    info.configure_char("Name", value=device.name)
    info.configure_char("Manufacturer", value=device.manufacturer or device.provider)
    info.configure_char("Model", value=device.model or str(device.kind))
    info.configure_char("SerialNumber", value=device.id)
    info.configure_char("FirmwareRevision", value=firmware)


def _configure_child_info(
    accessory: Accessory, device: Device, label: str, firmware: str
) -> None:
    info = accessory.get_service("AccessoryInformation")
    if info is None:
        return
    info.configure_char("Name", value=f"{device.name} {label}")
    info.configure_char("Manufacturer", value=device.manufacturer or device.provider)
    info.configure_char("Model", value=device.model or str(device.kind))
    info.configure_char("SerialNumber", value=f"{device.id}#{label}")
    info.configure_char("FirmwareRevision", value=firmware)


def build_accessory(
    driver: AccessoryDriver,
    device: Device,
    manager: DeviceManager,
    schedule: Schedule,
    firmware: str,
) -> DeviceAccessory:
    accessory = Accessory(driver, device.name)
    _configure_info(accessory, device, firmware)
    wrapper = DeviceAccessory(accessory, device.id)
    specs = SPEC_BUILDERS.get(device.kind, _specs_for_sensor)(device)
    specs = [_bind_context(spec, manager, device, schedule) for spec in specs]
    wrapper.bind(driver, specs, device)

    extras: list[tuple[str, Capability, bool]] = []
    if Capability.DISPLAY in device.capabilities:
        extras.append(("显示屏", Capability.DISPLAY, True))
    # AirPurifier exposes LockPhysicalControls natively; other services do
    # not, so their child lock becomes a small child switch.
    if (
        Capability.CHILD_LOCK in device.capabilities
        and device.kind is not DeviceKind.PURIFIER
    ):
        extras.append(("童锁", Capability.CHILD_LOCK, False))
    for label, capability, default in (
        ("UV 杀菌", Capability.UV, False),
        ("负离子", Capability.ANION, False),
        ("提示音", Capability.BUZZER, True),
        ("智能托管", Capability.SMART_MODE, False),
        ("夜间智能", Capability.SMART_NIGHT, True),
        ("移动侦测", Capability.MOTION_DETECTION, True),
        ("宽动态", Capability.CAMERA_WDR, True),
        ("全彩夜视", Capability.CAMERA_FULL_COLOR, False),
        ("移动追踪", Capability.CAMERA_TRACKING, False),
        ("时间水印", Capability.CAMERA_WATERMARK, False),
        ("地毯增压", Capability.CARPET_BOOST, False),
        ("自动集尘", Capability.AUTO_DUST_ARREST, False),
        ("自动烘干", Capability.AUTO_MOP_DRY, False),
        ("请勿打扰", Capability.NO_DISTURB, False),
        ("AI 清扫", Capability.AI_CLEANING, False),
        ("睡眠模式", Capability.SLEEP_MODE, False),
        ("除螨", Capability.MITE_REMOVAL, False),
        ("报警音", Capability.ALARM, True),
    ):
        if capability in device.capabilities:
            extras.append((label, capability, default))
    config = getattr(manager, "config", None)
    extra_mode = getattr(getattr(config, "gateway", None), "extra_switches", "auto")
    if extra_mode == "none":
        extras = []
    elif extra_mode == "minimal":
        # Keep the desk lamp and electric blanket complete; all other devices
        # expose only their native services/readings in HomeKit.
        if device.kind not in (DeviceKind.LIGHT, DeviceKind.THERMOSTAT):
            extras = []
    elif extra_mode == "auto":
        extras = [item for item in extras if item[1] in AUTO_EXTRA_SWITCHES]

    # Each extra is its own accessory so the Home app can show a readable
    # name. Multiple services in one accessory all inherit the accessory name,
    # which is why the previous grouping looked like duplicated switches.
    for label, capability, default in extras:
        child = Accessory(driver, f"{device.name} {label}")
        _configure_child_info(child, device, label, firmware)
        child_specs = [
            CharSpec(
                SWITCH,
                "On",
                lambda d, cap=capability, dv=default: bool(d.get(cap, dv)),
                lambda value, cap=capability: Command(cap, bool(value)),
                {capability},
            )
        ]
        child_specs = [
            _bind_context(spec, manager, device, schedule) for spec in child_specs
        ]
        child_wrapper = DeviceAccessory(child, device.id)
        child_wrapper.bind(driver, child_specs, device)
        wrapper._children.append(child_wrapper)
    return wrapper
