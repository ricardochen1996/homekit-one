"""HAP bridge lifecycle, pairing state and QR generation."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import zlib
from pathlib import Path
from typing import Any

import segno
from pyhap import util
from pyhap.accessory import Bridge
from pyhap.accessory_driver import AccessoryDriver

from homeone import __version__
from homeone.config import Config
from homeone.core.manager import DeviceManager
from homeone.core.model import Device
from homeone.homekit.accessories import DeviceAccessory, build_accessory

_LOGGER = logging.getLogger(__name__)


class HomeKitBridge:
    def __init__(self, config: Config, manager: DeviceManager) -> None:
        self.config = config
        self.manager = manager
        self.state_dir = config.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_running_loop()
        pincode = resolve_pincode(self.state_dir, config.gateway.homekit_pincode)
        self.driver = AccessoryDriver(
            port=config.gateway.homekit_port,
            pincode=pincode.encode("ascii"),
            persist_file=str(self.state_dir / "accessory.state"),
            loop=self._loop,
        )
        self.bridge = Bridge(self.driver, config.gateway.name)
        self.driver.add_accessory(self.bridge)
        try:
            os.chmod(self.driver.persist_file, 0o600)
        except OSError:
            pass
        self._wrappers: dict[str, list[DeviceAccessory]] = {}
        self._used_aids: set[int] = {1}
        self._started = False
        self._build()
        manager.add_listener(self._on_state)

    # ---- building ------------------------------------------------------
    def _build(self) -> None:
        for device in self.manager.all_devices():
            self._add_device(device)

    def _add_device(self, device: Device) -> None:
        wrapper = build_accessory(
            self.driver,
            device,
            self.manager,
            self._schedule,
            firmware=__version__,
        )
        for accessory in wrapper.all_accessories():
            # Stable AIDs keep iOS from rebuilding its accessory cache every
            # time a device is added or a provider changes iteration order.
            accessory.aid = self._stable_aid(
                f"{device.id}|{accessory.display_name}"
            )
            self.bridge.add_accessory(accessory)
        self._wrappers.setdefault(device.id, []).append(wrapper)

    def _stable_aid(self, key: str) -> int:
        candidate = zlib.crc32(key.encode("utf-8")) % 100_000 + 2
        while candidate in self._used_aids or candidate == 7:
            candidate = candidate + 1 if candidate < 100_002 else 2
        self._used_aids.add(candidate)
        return candidate

    def _schedule(self, coro: Any) -> None:
        task = self._loop.create_task(coro)
        task.add_done_callback(_log_task_error)

    # ---- state fan-out -------------------------------------------------
    def _on_state(self, device: Device, changed: set[Any]) -> None:
        for wrapper in self._wrappers.get(device.id, []):
            wrapper.update(device, changed)

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        await self.driver.async_start()
        self._started = True
        _LOGGER.info(
            "HomeKit bridge '%s' listening on port %s",
            self.config.gateway.name,
            self.config.gateway.homekit_port,
        )

    async def stop(self) -> None:
        self.manager.remove_listener(self._on_state)
        if self._started:
            await self.driver.async_stop()
            self._started = False

    # ---- pairing -------------------------------------------------------
    @property
    def pincode(self) -> str:
        return self.driver.state.pincode.decode("ascii")

    @property
    def paired(self) -> bool:
        return bool(getattr(self.driver.state, "paired", False))

    def pairing_info(self) -> dict[str, Any]:
        return {
            "name": self.config.gateway.name,
            "pincode": self.pincode,
            "uri": self.bridge.xhm_uri(),
            "paired": self.paired,
            "port": self.config.gateway.homekit_port,
            "accessories": sum(
                len(wrapper.all_accessories())
                for wrappers in self._wrappers.values()
                for wrapper in wrappers
            ),
        }

    def qr_png(self) -> bytes:
        buffer = io.BytesIO()
        segno.make(self.bridge.xhm_uri(), error="m").save(
            buffer, kind="png", scale=7, border=3, dark="#111111", light="#ffffff"
        )
        return buffer.getvalue()

    async def reset_pairing(self) -> None:
        """Forget all HomeKit pairings. The caller must restart the process."""
        if self._started:
            await self.driver.async_stop()
            self._started = False
        Path(self.driver.persist_file).unlink(missing_ok=True)
        (self.state_dir / "pairing.json").unlink(missing_ok=True)
        _LOGGER.warning("HomeKit pairing state removed; restart the gateway")


def _log_task_error(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        _LOGGER.exception("background HomeKit task failed")


PINCODE_RE = re.compile(r"^\d{3}-\d{2}-\d{3}$")


def resolve_pincode(state_dir: Path, configured: str | None) -> str:
    """Keep one stable pincode across restarts.

    HAP-python persists paired clients but not the pincode, so without this a
    restart would silently rotate the code shown by ``homeone pairing``.
    """
    if configured:
        return configured
    path = state_dir / "pairing.json"
    if path.exists():
        try:
            value = str(json.loads(path.read_text("utf-8")).get("pincode", ""))
            if PINCODE_RE.match(value):
                return value
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    pincode = util.generate_pincode().decode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pincode": pincode}) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return pincode
