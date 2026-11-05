from __future__ import annotations

from homekit_one.core.model import Capability, Command, DeviceKind, Mode
from homekit_one.providers.mijia_cloud.spec import build_cloud_spec


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


def action(siid: int, name: str, aiid: int = 1, inputs: tuple[int, ...] = ()):
    return {
        "iid": aiid,
        "type": f"urn:miot-spec-v2:action:{name}:00002800:test:1",
        "in": list(inputs),
        "out": [],
        "description": name,
    }


def service_with_actions(siid: int, name: str, props: list[dict], actions: list[dict]):
    data = service(siid, name, props)
    data["actions"] = actions
    return data


def test_camera_spec():
    data = spec(
        "camera",
        [
            service(2, "camera-control", [prop(2, 1, "on")]),
            service(
                5,
                "motion-detection",
                [prop(5, 1, "motion-detection", "bool", ("read", "write", "notify"))],
            ),
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "chuangmi.camera.test")
    assert cloud is not None
    assert cloud.kind is DeviceKind.CAMERA
    assert cloud.capabilities == {
        Capability.POWER,
        Capability.MOTION,
        Capability.MOTION_DETECTION,
    }
    values = cloud.decode({(2, 1): True, (5, 1): True})
    assert values[Capability.POWER] is True
    assert values[Capability.MOTION] is True
    assert cloud.encode(Command(Capability.POWER, False)) == [(2, 1, False)]


def test_vacuum_spec():
    data = spec(
        "vacuum",
        [
            service_with_actions(
                2,
                "vacuum",
                [
                    prop(2, 2, "status", "uint8", ("read", "notify")),
                    prop(2, 1, "battery-level", "uint8", ("read", "notify")),
                ],
                [
                    action(2, "start-sweep"),
                    action(2, "stop-and-gocharge", aiid=3),
                ],
            )
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "xiaomi.vacuum.test")
    assert cloud is not None
    assert cloud.kind is DeviceKind.VACUUM
    assert {Capability.POWER, Capability.BATTERY} <= cloud.capabilities
    assert cloud.decode({(2, 2): 4, (2, 1): 80})[Capability.POWER] is True
    assert cloud.decode({(2, 2): 2, (2, 1): 80})[Capability.POWER] is False
    assert cloud.encode(Command(Capability.POWER, True)) == []
    assert cloud.action_for(Command(Capability.POWER, True)).aiid == 1
    assert cloud.action_for(Command(Capability.POWER, False)).aiid == 3


def test_pet_feeder_spec():
    data = spec(
        "pet-feeder",
        [
            service_with_actions(
                2,
                "pet-feeder",
                [prop(2, 26, "status", "uint8", ("read", "notify"))],
                [action(2, "pet-food-out", inputs=(8,))],
            )
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "xiaomi.feeder.test")
    assert cloud is not None
    assert cloud.kind is DeviceKind.PET_FEEDER
    assert cloud.decode({(2, 26): 1})[Capability.POWER] is True
    assert cloud.decode({(2, 26): 0})[Capability.POWER] is False
    assert cloud.action_for(Command(Capability.POWER, True)).in_params == (
        {"piid": 8, "value": 1},
    )
    assert cloud.action_for(Command(Capability.POWER, False)) is None


def test_pet_waterer_spec():
    data = spec(
        "pet-drinking-fountain",
        [
            service(
                2,
                "pet-drinking-fountain",
                [
                    prop(
                        2,
                        4,
                        "mode",
                        "uint8",
                        **{
                            "value-list": [
                                {"value": 0, "description": "Auto"},
                                {"value": 1, "description": "Interval"},
                                {"value": 2, "description": "Constant"},
                            ]
                        },
                    ),
                    prop(2, 1, "battery-level", "uint8", ("read", "notify")),
                    prop(3, 1, "filter-life-level", "uint8", ("read", "notify")),
                ],
            )
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "xiaomi.pet_waterer.test")
    assert cloud is not None
    assert cloud.kind is DeviceKind.PET_WATERER
    assert cloud.decode({(2, 4): 2})[Capability.POWER] is True
    assert cloud.decode({(2, 4): 0})[Capability.POWER] is False
    assert cloud.encode(Command(Capability.POWER, True)) == [(2, 4, 2)]
    assert cloud.encode(Command(Capability.POWER, False)) == [(2, 4, 0)]


def test_speaker_spec():
    data = spec(
        "speaker",
        [
            service(
                2,
                "speaker",
                [
                    prop(2, 1, "volume", "uint8", **{"value-range": [0, 100, 1]}),
                    prop(2, 2, "mute"),
                ],
            ),
            service_with_actions(
                3,
                "play-control",
                [prop(3, 1, "playing-state", "uint8", ("read", "notify"))],
                [action(3, "play"), action(3, "pause", aiid=2)],
            ),
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "xiaomi.speaker.test")
    assert cloud is not None
    assert cloud.kind is DeviceKind.SPEAKER
    assert {Capability.VOLUME, Capability.MUTE, Capability.POWER} <= cloud.capabilities
    values = cloud.decode({(2, 1): 40, (2, 2): False, (3, 1): 1})
    assert values[Capability.VOLUME] == 40
    assert values[Capability.MUTE] is False
    assert values[Capability.POWER] is True
    assert cloud.encode(Command(Capability.VOLUME, 70)) == [(2, 1, 70)]
    assert cloud.encode(Command(Capability.MUTE, True)) == [(2, 2, 1)]
    assert cloud.action_for(Command(Capability.POWER, False)).aiid == 2


def test_camera_extra_switches():
    data = spec(
        "camera",
        [
            service(
                2,
                "camera-control",
                [
                    prop(2, 1, "on"),
                    prop(2, 5, "wdr-mode"),
                    prop(2, 6, "glimmer-full-color"),
                    prop(2, 8, "motion-tracking"),
                    prop(2, 9, "time-watermark"),
                ],
            ),
            service(
                5,
                "motion-detection",
                [prop(5, 1, "motion-detection", "bool", ("read", "write", "notify"))],
            ),
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "camera.test")
    assert cloud is not None
    assert {
        Capability.MOTION_DETECTION,
        Capability.CAMERA_WDR,
        Capability.CAMERA_FULL_COLOR,
        Capability.CAMERA_TRACKING,
        Capability.CAMERA_WATERMARK,
    } <= cloud.capabilities
    assert cloud.encode(Command(Capability.CAMERA_TRACKING, True)) == [(2, 8, 1)]
    values = cloud.decode({(5, 1): True, (2, 5): False})
    assert values[Capability.MOTION] is True
    assert values[Capability.MOTION_DETECTION] is True
    assert values[Capability.CAMERA_WDR] is False


def test_vacuum_extra_switches_and_filter_reset():
    data = spec(
        "vacuum",
        [
            service_with_actions(
                2,
                "vacuum",
                [
                    prop(2, 2, "status", "uint8", ("read", "notify")),
                    prop(2, 20, "carpet-boost"),
                    prop(2, 32, "auto-dust-arrest"),
                    prop(2, 34, "auto-mop-dry"),
                    prop(2, 37, "ai-cleaning"),
                    prop(5, 1, "physical-controls-locked"),
                    prop(4, 1, "alarm"),
                    prop(11, 1, "no-disturb"),
                ],
                [
                    action(2, "start-sweep"),
                    action(14, "reset-filter-life"),
                ],
            )
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "vacuum.test")
    assert cloud is not None
    assert {
        Capability.CARPET_BOOST,
        Capability.AUTO_DUST_ARREST,
        Capability.AUTO_MOP_DRY,
        Capability.AI_CLEANING,
        Capability.NO_DISTURB,
        Capability.CHILD_LOCK,
        Capability.ALARM,
        Capability.FILTER_RESET,
    } <= cloud.capabilities
    assert cloud.encode(Command(Capability.CARPET_BOOST, True)) == [(2, 20, 1)]
    assert cloud.action_for(Command(Capability.FILTER_RESET, True)).aiid == 1


def test_feeder_waterer_and_speaker_extra_switches():
    feeder = spec(
        "pet-feeder",
        [
            service_with_actions(
                2,
                "pet-feeder",
                [prop(2, 26, "status", "uint8", ("read", "notify"))],
                [action(2, "pet-food-out", inputs=(8,))],
            ),
            service(3, "physical-controls-locked", [prop(3, 1, "physical-controls-locked")]),
        ],
    )
    feeder_spec = build_cloud_spec(feeder, feeder["type"], "feeder.test")
    assert feeder_spec is not None
    assert Capability.CHILD_LOCK in feeder_spec.capabilities

    waterer = spec(
        "pet-drinking-fountain",
        [
            service_with_actions(
                2,
                "pet-drinking-fountain",
                [
                    prop(
                        2,
                        4,
                        "mode",
                        "uint8",
                        **{
                            "value-list": [
                                {"value": 0, "description": "Auto"},
                                {"value": 2, "description": "Constant"},
                            ]
                        },
                    ),
                    prop(3, 1, "filter-life-level", "uint8", ("read", "notify")),
                ],
                [action(3, "reset-filter-life")],
            ),
            service(4, "physical-controls-locked", [prop(4, 1, "physical-controls-locked")]),
            service(6, "no-disturb", [prop(6, 1, "no-disturb")]),
        ],
    )
    waterer_spec = build_cloud_spec(waterer, waterer["type"], "waterer.test")
    assert waterer_spec is not None
    assert {Capability.CHILD_LOCK, Capability.NO_DISTURB, Capability.FILTER_RESET} <= (
        waterer_spec.capabilities
    )

    speaker = spec(
        "speaker",
        [
            service(
                2,
                "speaker",
                [prop(2, 1, "volume", "uint8"), prop(2, 2, "mute")],
            ),
            service(5, "intelligent-speaker", [prop(5, 3, "sleep-mode")]),
        ],
    )
    speaker_spec = build_cloud_spec(speaker, speaker["type"], "speaker.test")
    assert speaker_spec is not None
    assert Capability.SLEEP_MODE in speaker_spec.capabilities
    assert speaker_spec.encode(Command(Capability.SLEEP_MODE, True)) == [(5, 3, 1)]


def test_blanket_extra_switches():
    data = spec(
        "electric-blanket",
        [
            service(
                2,
                "electric-blanket",
                [
                    prop(
                        2,
                        3,
                        "mode",
                        "uint8",
                        **{
                            "value-list": [
                                {"value": 0, "description": "Common"},
                                {"value": 1, "description": "Mite Removal"},
                            ]
                        },
                    ),
                    prop(2, 4, "target-temperature", "uint8"),
                    prop(2, 5, "temperature", "int8", ("read", "notify")),
                ],
            ),
            service(4, "alarm", [prop(4, 1, "alarm")]),
            service(5, "switch", [prop(5, 2, "on")]),
        ],
    )
    cloud = build_cloud_spec(data, data["type"], "blanket.test")
    assert cloud is not None
    assert Capability.MITE_REMOVAL in cloud.capabilities
    assert Capability.ALARM in cloud.capabilities
    assert cloud.encode(Command(Capability.MITE_REMOVAL, True)) == [(2, 3, 1)]
    assert cloud.decode({(2, 3): 1})[Capability.MITE_REMOVAL] is True
