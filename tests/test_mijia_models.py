from __future__ import annotations

from homeone.core.model import Capability, Command, Mode
from homeone.providers.mijia.models import MODELS


def test_rma1_decode():
    spec = MODELS["zhimi.airpurifier.rma1"]
    raw = {
        (2, 1): True,
        (2, 4): 2,
        (9, 2): 9,
        (3, 4): 12,
        (3, 7): 23.5,
        (3, 1): 50,
        (4, 1): 80,
        (8, 1): True,
        (13, 2): 2,
    }
    values = spec.decode(raw)
    assert values[Capability.POWER] is True
    assert values[Capability.MODE] == Mode.MANUAL.value
    assert values[Capability.FAN_SPEED] == 64
    assert values[Capability.PM25] == 12.0
    assert values[Capability.TEMPERATURE] == 23.5
    assert values[Capability.HUMIDITY] == 50.0
    assert values[Capability.FILTER_LIFE] == 80.0
    assert values[Capability.CHILD_LOCK] is True
    assert values[Capability.DISPLAY] is True


def test_rma1_fan_command_selects_preset_and_mode():
    spec = MODELS["zhimi.airpurifier.rma1"]
    writes = spec.encode(Command(Capability.FAN_SPEED, 100))
    assert (9, 2, 14) in writes
    assert (2, 4, 2) in writes
    assert (2, 1, 1) in writes


def test_rma1_fan_off_turns_power_off():
    spec = MODELS["zhimi.airpurifier.rma1"]
    assert spec.encode(Command(Capability.FAN_SPEED, 0)) == [(2, 1, 0)]


def test_rma1_mode_mapping():
    spec = MODELS["zhimi.airpurifier.rma1"]
    assert spec.encode(Command(Capability.MODE, Mode.AUTO.value)) == [(2, 4, 0)]
    assert spec.encode(Command(Capability.MODE, Mode.SLEEP.value)) == [(2, 4, 1)]


def test_p28_fan_percent_is_direct():
    spec = MODELS["dmaker.fan.p28"]
    writes = spec.encode(Command(Capability.FAN_SPEED, 55))
    assert (8, 1, 55) in writes
    assert (2, 1, 1) in writes
    values = spec.decode({(8, 1): 55, (2, 1): True, (2, 3): 2, (2, 4): 1})
    assert values[Capability.FAN_SPEED] == 55
    assert values[Capability.MODE] == Mode.AUTO.value
    assert values[Capability.SWING] is True


def test_plug_derives_outlet_in_use():
    spec = MODELS["chuangmi.plug.m3"]
    values = spec.decode({(2, 1): True, (2, 2): 30.0})
    assert values[Capability.OUTLET_IN_USE] is True
    assert Capability.TEMPERATURE in spec.capabilities


def test_light_color_temperature_clamped():
    spec = MODELS["yeelink.light.lamp4"]
    writes = spec.encode(Command(Capability.COLOR_TEMP, 2000))
    assert writes == [(2, 3, 2600)]
    writes = spec.encode(Command(Capability.COLOR_TEMP, 9000))
    assert writes == [(2, 3, 5000)]
