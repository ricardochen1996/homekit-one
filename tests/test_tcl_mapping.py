from __future__ import annotations

from homeone.core.model import Capability, Command, Mode
from homeone.providers.tcl.mapping import decode_shadow, encode_command, is_ac_device

WORK_MODES = {"auto": 0, "cool": 1, "dry": 2, "fan": 3, "heat": 4}


def test_ac_device_types():
    assert is_ac_device("Split AC")
    assert is_ac_device("split ac fresh air")
    assert not is_ac_device("breeva A2")
    assert not is_ac_device("Dehumidifier DEM")


def test_decode_split_ac_shadow():
    raw = {
        "powerSwitch": 1,
        "targetTemperature": 24,
        "currentTemperature": 26,
        "workMode": 1,
        "windSpeed": 4,
        "verticalSwitch": 1,
    }
    values = decode_shadow(raw, WORK_MODES)
    assert values[Capability.POWER] is True
    assert values[Capability.TARGET_TEMP] == 24.0
    assert values[Capability.CURRENT_TEMP] == 26.0
    assert values[Capability.MODE] == Mode.COOL.value
    assert values[Capability.FAN_SPEED] == 50
    assert values[Capability.SWING] is True


def test_decode_unknown_mode_is_ignored():
    values = decode_shadow({"workMode": 99}, WORK_MODES)
    assert Capability.MODE not in values


def test_encode_commands():
    assert encode_command(Command(Capability.POWER, False), WORK_MODES) == {"powerSwitch": 0}
    assert encode_command(Command(Capability.MODE, Mode.HEAT.value), WORK_MODES) == {"workMode": 4}
    assert encode_command(Command(Capability.TARGET_TEMP, 25.6), WORK_MODES) == {"targetTemperature": 26}
    assert encode_command(Command(Capability.SWING, True), WORK_MODES) == {"verticalSwitch": 1}
    fan = encode_command(Command(Capability.FAN_SPEED, 55), WORK_MODES)
    assert fan == {"windSpeed": 4, "windSpeedAutoSwitch": 0}


def test_custom_work_mode_order():
    custom = {"auto": 0, "heat": 1, "cool": 2, "dry": 3, "fan": 4}
    assert decode_shadow({"workMode": 1}, custom)[Capability.MODE] == Mode.HEAT.value
    assert encode_command(Command(Capability.MODE, Mode.COOL.value), custom) == {"workMode": 2}
