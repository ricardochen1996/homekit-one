"""Devices added through the web UI, persisted separately from config."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class DeviceRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {"mijia": []}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if isinstance(data, dict):
                self._data = {"mijia": list(data.get("mijia") or [])}
        except (OSError, json.JSONDecodeError):
            self._data = {"mijia": []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".devices-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ---- Mijia ---------------------------------------------------------
    def mijia_entries(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._data.get("mijia", []) if item.get("id")]

    def get_mijia(self, device_id: str) -> dict[str, Any] | None:
        for item in self._data.get("mijia", []):
            if str(item.get("id")) == str(device_id):
                return dict(item)
        return None

    def upsert_mijia(self, entry: dict[str, Any]) -> None:
        entry = {key: value for key, value in entry.items() if value not in (None, "")}
        if not entry.get("id"):
            raise ValueError("米家设备必须有 id")
        entries = self._data.setdefault("mijia", [])
        for index, item in enumerate(entries):
            if str(item.get("id")) == str(entry["id"]):
                entries[index] = {**item, **entry}
                break
        else:
            entries.append(entry)
        self.save()

    def remove_mijia(self, device_id: str) -> None:
        self._data["mijia"] = [
            item
            for item in self._data.get("mijia", [])
            if str(item.get("id")) != str(device_id)
        ]
        self.save()
