from __future__ import annotations

from homekit_one.core.model import Capability, Device, DeviceKind
from homekit_one.homekit.accessories import (
    _specs_for_camera,
    _specs_for_feeder,
    _specs_for_speaker,
    _specs_for_vacuum,
    _specs_for_waterer,
)


def _device(kind: DeviceKind, capabilities: set[Capability]) -> Device:
    return Device(
        id=f"test:{kind}",
        name="测试设备",
        provider="test",
        kind=kind,
        capabilities=capabilities,
    )


def _bindings(specs):
    return {(spec.service, spec.name) for spec in specs}


def test_vacuum_services():
    device = _device(
        DeviceKind.VACUUM,
        {
            Capability.POWER,
            Capability.BATTERY,
            Capability.FILTER_LIFE,
            Capability.FILTER_RESET,
        },
    )
    bindings = _bindings(_specs_for_vacuum(device))
    assert ("Switch", "On") in bindings
    assert ("BatteryService", "BatteryLevel") in bindings
    assert ("FilterMaintenance", "FilterLifeLevel") in bindings
    assert ("FilterMaintenance", "ResetFilterIndication") in bindings


def test_camera_services():
    device = _device(DeviceKind.CAMERA, {Capability.POWER, Capability.MOTION})
    bindings = _bindings(_specs_for_camera(device))
    assert ("Switch", "On") in bindings
    assert ("MotionSensor", "MotionDetected") in bindings


def test_feeder_services():
    device = _device(DeviceKind.PET_FEEDER, {Capability.POWER})
    assert _bindings(_specs_for_feeder(device)) == {("Switch", "On")}


def test_waterer_services():
    device = _device(
        DeviceKind.PET_WATERER,
        {
            Capability.POWER,
            Capability.BATTERY,
            Capability.FILTER_LIFE,
            Capability.FILTER_RESET,
        },
    )
    bindings = _bindings(_specs_for_waterer(device))
    assert ("Valve", "Active") in bindings
    assert ("Valve", "InUse") in bindings
    assert ("Valve", "ValveType") in bindings
    assert ("BatteryService", "BatteryLevel") in bindings
    assert ("FilterMaintenance", "FilterLifeLevel") in bindings
    assert ("FilterMaintenance", "ResetFilterIndication") in bindings


def test_speaker_services():
    device = _device(
        DeviceKind.SPEAKER,
        {Capability.POWER, Capability.VOLUME, Capability.MUTE},
    )
    bindings = _bindings(_specs_for_speaker(device))
    assert ("Speaker", "Mute") in bindings
    assert ("Speaker", "Volume") in bindings
    assert ("Switch", "On") in bindings
