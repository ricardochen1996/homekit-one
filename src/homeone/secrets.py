"""Small local secret store.

A JSON file with mode 0600 is deliberate: a launchd service cannot reliably
unlock the macOS login keychain without user interaction, and pretending to
encrypt with a key stored next to the ciphertext would be worse than being
honest about file permissions. The state directory itself is 0700.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class SecretsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        else:
            self._data = {}
        return self._data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".secrets-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def section(self, name: str) -> dict[str, Any]:
        value = self._data.get(name, {})
        return dict(value) if isinstance(value, dict) else {}

    def get(self, section: str, key: str, default: Any = None) -> Any:
        value = self._data.get(section, {}).get(key)
        return default if value in (None, "") else value

    def set(self, section: str, key: str, value: Any) -> None:
        self._data.setdefault(section, {})[key] = value
        self.save()

    def set_many(self, section: str, values: dict[str, Any]) -> None:
        self._data.setdefault(section, {}).update(values)
        self.save()

    def delete(self, section: str, key: str) -> None:
        self._data.get(section, {}).pop(key, None)
        self.save()

    def redacted(self) -> dict[str, Any]:
        def mask(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: mask(v) for k, v in value.items()}
            if isinstance(value, str) and len(value) > 6:
                return value[:2] + "…" + value[-2:]
            if value:
                return "•••"
            return value

        return mask(self._data)
