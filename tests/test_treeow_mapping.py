from __future__ import annotations

import json

from homekit_one.core.model import Capability, Command, Mode
from homekit_one.providers.treeow.mapping import decode_props, encode_command
from homekit_one.providers.treeow.provider import _extract_props


def test_decode_props():
    values = decode_props(
        {
            "switch": True,
            "mode": 1,
            "fan_speed_enum": 4,
            "pm25": 18,
            "temp_indoor": 23.5,
            "humidity": 48,
            "filter_life": 76,
            "child_lock": False,
            "light": True,
        }
    )
    assert values[Capability.POWER] is True
    assert values[Capability.MODE] == Mode.AUTO.value
    assert values[Capability.FAN_SPEED] == 57
    assert values[Capability.PM25] == 18.0
    assert values[Capability.TEMPERATURE] == 23.5
    assert values[Capability.HUMIDITY] == 48.0
    assert values[Capability.FILTER_LIFE] == 76.0
    assert values[Capability.CHILD_LOCK] is False
    assert values[Capability.DISPLAY] is True


def test_encode_commands():
    assert encode_command(Command(Capability.POWER, False)) == [("switch", False)]
    assert encode_command(Command(Capability.MODE, Mode.AUTO.value)) == [("mode", 1)]
    assert encode_command(Command(Capability.CHILD_LOCK, True)) == [("child_lock", True)]
    assert encode_command(Command(Capability.DISPLAY, False)) == [("light", False)]


def test_fan_speed_zero_means_sleep():
    assert encode_command(Command(Capability.FAN_SPEED, 0)) == [("mode", 3), ("fan_speed_enum", 0)]


def test_fan_speed_selects_nearest_gear():
    pairs = encode_command(Command(Capability.FAN_SPEED, 80))
    assert pairs[0] == ("mode", 0)
    assert pairs[1][1] == 5  # 71% is nearest to 80


def test_extract_props_from_device_info():
    payload = {"Air_Purifier": {"switch": 1, "pm25": 9, "pm25&timestamp": 123}}
    info = {"props": [{"value": json.dumps(payload)}]}
    assert _extract_props(info) == {"switch": 1, "pm25": 9}


def test_extract_props_handles_bad_json():
    assert _extract_props({"props": [{"value": "not-json"}]}) == {}
    assert _extract_props({"props": []}) == {}


def test_decode_extended_features():
    values = decode_props(
        {
            "ch2o_value": 18,
            "filter_days": 160,
            "uv": True,
            "anion": False,
            "speaker_onoff": True,
            "smart_mode_switch": False,
            "smart_night_switch": True,
            "online_state": 0,
        }
    )
    assert values[Capability.VOC] == 18.0
    assert values[Capability.FILTER_DAYS] == 160
    assert values[Capability.UV] is True
    assert values[Capability.ANION] is False
    assert values[Capability.BUZZER] is True
    assert values[Capability.SMART_MODE] is False
    assert values[Capability.SMART_NIGHT] is True


def test_encode_extended_features():
    assert encode_command(Command(Capability.UV, False)) == [("uv", False)]
    assert encode_command(Command(Capability.ANION, True)) == [("anion", True)]
    assert encode_command(Command(Capability.BUZZER, False)) == [
        ("speaker_onoff", False)
    ]
    assert encode_command(Command(Capability.SMART_MODE, True)) == [
        ("smart_mode_switch", True)
    ]
    assert encode_command(Command(Capability.SMART_NIGHT, False)) == [
        ("smart_night_switch", False)
    ]
    assert encode_command(Command(Capability.FILTER_RESET, True)) == [
        ("filter_reset", True),
        ("filter_warn_reset", True),
    ]


def test_fan_speed_full_means_comfort():
    assert encode_command(Command(Capability.FAN_SPEED, 100)) == [
        ("mode", 2),
        ("fan_speed_enum", 6),
    ]


def test_online_state_zero_means_online():
    from homekit_one.providers.treeow.mapping import online_state_is_offline

    assert online_state_is_offline({"online_state": 0}) is False
    assert online_state_is_offline({"online_state": "0"}) is False
    assert online_state_is_offline({"online_state": 1}) is True
    assert online_state_is_offline({"online_state": "1"}) is True
    assert online_state_is_offline({"online_state": False}) is True
    assert online_state_is_offline({"online_state": True}) is False
    assert online_state_is_offline({}) is False
