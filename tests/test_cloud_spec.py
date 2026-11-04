from __future__ import annotations

from homeone.core.model import Capability, Command, DeviceKind, Mode
from homeone.providers.mijia_cloud.spec import build_cloud_spec


def prop(
    siid: int,
    piid: int,
    name: str,
    fmt: str = "bool",
    access: tuple[str, ...] = ("read", "write", "notify"),
    **extra,
):
    return {
        "iid": piid,
        "type": f"urn:miot-spec-v2:property:{name}:00000000:test:1",
        "format": fmt,
        "access": list(access),
        "description": extra.pop("description", name),
        **extra,
    }


def service(siid: int, name: str, props: list[dict]):
    return {
        "iid": siid,
        "type": f"urn:miot-spec-v2:service:{name}:00007800:test:1",
        "properties": props,
    }


def spec(device_type: str, services: list[dict]):
    return {
        "type": f"urn:miot-spec-v2:device:{device_type}:0000A001:test:1",
        "services": services,
    }


def test_light_spec():
    data = spec(
        "light",
        [
            service(
                2,
                "light",
                [
                    prop(2, 1, "on"),
                    prop(2, 2, "brightness", "uint8", **{"value-range": [1, 100, 1]}),
                    prop(
                        2,
                        3,
                        "color-temperature",
                        "uint32",
                        **{"value-range": [2700, 6500, 1]},
                    ),
                ],
            )
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "test.light")
    assert cloud is not None
    assert cloud.kind is DeviceKind.LIGHT
    assert cloud.capabilities == {
        Capability.POWER,
        Capability.BRIGHTNESS,
        Capability.COLOR_TEMP,
    }
    values = cloud.decode({(2, 1): True, (2, 2): 50, (2, 3): 4000})
    assert values[Capability.POWER] is True
    assert values[Capability.BRIGHTNESS] == 50
    assert values[Capability.COLOR_TEMP] == 4000
    assert cloud.encode(Command(Capability.POWER, False)) == [(2, 1, False)]
    assert cloud.encode(Command(Capability.BRIGHTNESS, 80)) == [(2, 2, 80)]
    assert cloud.encode(Command(Capability.COLOR_TEMP, 2000)) == [(2, 3, 2700)]


def test_purifier_spec():
    data = spec(
        "air-purifier",
        [
            service(
                2,
                "air-purifier",
                [
                    prop(2, 1, "on"),
                    prop(
                        2,
                        4,
                        "mode",
                        "uint8",
                        **{
                            "value-list": [
                                {"value": 0, "description": "Auto"},
                                {"value": 1, "description": "Sleep"},
                                {"value": 2, "description": "Favorite"},
                            ]
                        },
                    ),
                    prop(
                        2,
                        2,
                        "fan-level",
                        "uint8",
                        **{
                            "value-list": [
                                {"value": 1, "description": "Level1"},
                                {"value": 2, "description": "Level2"},
                                {"value": 3, "description": "Level3"},
                                {"value": 4, "description": "Level4"},
                            ]
                        },
                    ),
                ],
            ),
            service(
                3,
                "environment",
                [
                    prop(3, 1, "relative-humidity", "uint8", ("read", "notify")),
                    prop(3, 4, "pm2.5-density", "uint16", ("read", "notify")),
                    prop(3, 7, "temperature", "float", ("read", "notify")),
                ],
            ),
            service(
                4,
                "filter",
                [prop(4, 1, "filter-life-level", "uint8", ("read", "notify"))],
            ),
            service(
                8,
                "physical-controls-locked",
                [prop(8, 1, "physical-controls-locked")],
            ),
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "test.purifier")
    assert cloud is not None
    assert cloud.kind is DeviceKind.PURIFIER
    assert {
        Capability.POWER,
        Capability.MODE,
        Capability.FAN_SPEED,
        Capability.PM25,
        Capability.TEMPERATURE,
        Capability.HUMIDITY,
        Capability.FILTER_LIFE,
        Capability.CHILD_LOCK,
    } <= cloud.capabilities

    values = cloud.decode(
        {
            (2, 1): True,
            (2, 4): 0,
            (2, 2): 4,
            (3, 1): 50,
            (3, 4): 12,
            (3, 7): 23.5,
            (4, 1): 80,
            (8, 1): True,
        }
    )
    assert values[Capability.MODE] == Mode.AUTO.value
    assert values[Capability.FAN_SPEED] == 100
    assert values[Capability.PM25] == 12
    assert values[Capability.FILTER_LIFE] == 80
    assert cloud.encode(Command(Capability.MODE, Mode.SLEEP.value)) == [(2, 4, 1)]
    assert cloud.encode(Command(Capability.FAN_SPEED, 80)) == [(2, 2, 3)]
    assert cloud.encode(Command(Capability.FAN_SPEED, 0)) == [(2, 1, False)]


def test_sensor_and_switch_specs():
    sensor_data = spec(
        "sensor",
        [
            service(
                2,
                "environment",
                [
                    prop(2, 1, "temperature", "float", ("read", "notify")),
                    prop(2, 2, "relative-humidity", "uint8", ("read", "notify")),
                    prop(2, 3, "pm2.5-density", "uint16", ("read", "notify")),
                ],
            )
        ],
    )
    sensor = build_cloud_spec(sensor_data, sensor_data["type"], "test.sensor")
    assert sensor is not None
    assert sensor.kind is DeviceKind.SENSOR
    assert sensor.capabilities == {
        Capability.TEMPERATURE,
        Capability.HUMIDITY,
        Capability.PM25,
    }

    switch_data = spec(
        "switch",
        [service(2, "switch", [prop(2, 1, "on")])],
    )
    switch = build_cloud_spec(switch_data, switch_data["type"], "test.switch")
    assert switch is not None
    assert switch.kind is DeviceKind.SWITCH
    assert switch.capabilities == {Capability.POWER}


def test_ac_spec():
    data = spec(
        "air-conditioner",
        [
            service(
                2,
                "air-conditioner",
                [
                    prop(2, 1, "on"),
                    prop(
                        2,
                        2,
                        "mode",
                        "uint8",
                        **{
                            "value-list": [
                                {"value": 0, "description": "Auto"},
                                {"value": 1, "description": "Cool"},
                                {"value": 2, "description": "Heat"},
                                {"value": 3, "description": "Dry"},
                                {"value": 4, "description": "Fan"},
                            ]
                        },
                    ),
                    prop(
                        2,
                        3,
                        "target-temperature",
                        "float",
                        **{"value-range": [16, 30, 1]},
                    ),
                    prop(
                        2,
                        4,
                        "current-temperature",
                        "float",
                        ("read", "notify"),
                        **{"value-range": [-30, 100, 0.1]},
                    ),
                    prop(
                        2,
                        5,
                        "fan-level",
                        "uint8",
                        **{
                            "value-list": [
                                {"value": 1, "description": "Low"},
                                {"value": 2, "description": "Medium"},
                                {"value": 3, "description": "High"},
                            ]
                        },
                    ),
                    prop(2, 6, "vertical-swing"),
                ],
            )
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "test.ac")
    assert cloud is not None
    assert cloud.kind is DeviceKind.AC
    assert {
        Capability.POWER,
        Capability.MODE,
        Capability.TARGET_TEMP,
        Capability.CURRENT_TEMP,
        Capability.FAN_SPEED,
        Capability.SWING,
    } <= cloud.capabilities
    values = cloud.decode({(2, 1): True, (2, 2): 1, (2, 3): 25.5, (2, 4): 27.0})
    assert values[Capability.MODE] == Mode.COOL.value
    assert values[Capability.TARGET_TEMP] == 25.5
    assert values[Capability.CURRENT_TEMP] == 27.0
    assert cloud.encode(Command(Capability.MODE, Mode.HEAT.value)) == [(2, 2, 2)]
    assert cloud.encode(Command(Capability.TARGET_TEMP, 25.6)) == [(2, 3, 25.6)]
    assert cloud.encode(Command(Capability.FAN_SPEED, 50)) == [(2, 5, 2)]


def test_curtain_is_skipped():
    data = spec(
        "curtain",
        [service(2, "curtain", [prop(2, 1, "on")])],
    )
    assert build_cloud_spec(data, data["type"], "test.curtain") is None


def test_electric_blanket_thermostat_spec():
    data = spec(
        "electric-blanket",
        [
            service(
                2,
                "electric-blanket",
                [
                    prop(2, 5, "temperature", "int8", ("read", "notify")),
                    prop(
                        2,
                        4,
                        "target-temperature",
                        "uint8",
                        **{"value-range": [25, 45, 1]},
                    ),
                ],
            ),
            service(
                3,
                "physical-controls-locked",
                [prop(3, 1, "physical-controls-locked")],
            ),
            service(5, "switch", [prop(5, 2, "on")]),
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "xiaomi.blanket.mj2")
    assert cloud is not None
    assert cloud.kind is DeviceKind.THERMOSTAT
    assert cloud.capabilities == {
        Capability.POWER,
        Capability.TARGET_TEMP,
        Capability.CURRENT_TEMP,
        Capability.CHILD_LOCK,
    }
    assert cloud.target_temp_range == (25.0, 45.0)
    values = cloud.decode({(5, 2): True, (2, 4): 32, (2, 5): 30, (3, 1): False})
    assert values[Capability.POWER] is True
    assert values[Capability.TARGET_TEMP] == 32
    assert values[Capability.CURRENT_TEMP] == 30
    assert values[Capability.CHILD_LOCK] is False
    assert cloud.encode(Command(Capability.TARGET_TEMP, 40)) == [(2, 4, 40)]
    assert cloud.encode(Command(Capability.POWER, False)) == [(5, 2, False)]
