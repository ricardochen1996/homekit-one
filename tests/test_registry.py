from __future__ import annotations

from homekit_one.registry import DeviceRegistry


def test_upsert_and_remove(tmp_path):
    registry = DeviceRegistry(tmp_path / "devices.json")
    registry.upsert_mijia({"id": "1", "name": "A", "model": "m", "host": "h"})
    registry.upsert_mijia({"id": "1", "name": "B"})
    entries = registry.mijia_entries()
    assert len(entries) == 1
    assert entries[0]["name"] == "B"
    assert entries[0]["host"] == "h"
    registry.remove_mijia("1")
    assert registry.mijia_entries() == []


def test_reload_persisted(tmp_path):
    path = tmp_path / "devices.json"
    registry = DeviceRegistry(path)
    registry.upsert_mijia({"id": "2", "name": "C", "model": "m", "host": "h"})
    reloaded = DeviceRegistry(path)
    assert reloaded.mijia_entries()[0]["name"] == "C"
