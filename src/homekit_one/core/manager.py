"""Device registry, polling, command serialization and listeners."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from homekit_one.config import Config
from homekit_one.core.model import Command, Device
from homekit_one.providers.base import Provider

_LOGGER = logging.getLogger(__name__)

StateListener = Callable[[Device, set[Any]], None]


class DeviceManager:
    def __init__(self, config: Config, providers: list[Provider]) -> None:
        self.config = config
        self.providers = {provider.name: provider for provider in providers}
        self.devices: dict[str, Device] = {}
        self._poll_tasks: dict[str, asyncio.Task[None]] = {}
        self._command_locks: dict[str, asyncio.Lock] = {}
        self._listeners: list[StateListener] = []
        self._reload_lock = asyncio.Lock()
        self._stopping = False

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        # Providers are independent; a slow cloud login must not delay the
        # local-only devices or the HomeKit bridge startup.
        await asyncio.gather(
            *(self._start_provider(provider) for provider in self.providers.values())
        )
        await self.reload()

    async def _start_provider(self, provider: Provider) -> None:
        try:
            await asyncio.wait_for(provider.start(), timeout=30)
        except Exception:
            _LOGGER.exception("provider %s failed to start", provider.name)

    async def stop(self) -> None:
        self._stopping = True
        for task in self._poll_tasks.values():
            task.cancel()
        if self._poll_tasks:
            await asyncio.gather(*self._poll_tasks.values(), return_exceptions=True)
        self._poll_tasks.clear()
        for provider in self.providers.values():
            try:
                await provider.stop()
            except Exception:
                _LOGGER.exception("provider %s failed to stop", provider.name)

    async def reload(self) -> None:
        """Re-list devices from every provider and reconcile poll tasks."""
        async with self._reload_lock:
            seen: set[str] = set()
            failed: set[str] = set()
            providers = list(self.providers.values())
            results = await asyncio.gather(
                *(self._list_provider(provider) for provider in providers),
                return_exceptions=True,
            )
            for provider, outcome in zip(providers, results, strict=False):
                if isinstance(outcome, BaseException):
                    _LOGGER.warning("provider %s list_devices failed: %s", provider.name, outcome)
                    failed.add(provider.name)
                    continue
                devices = outcome
                for device in devices:
                    seen.add(device.id)
                    existing = self.devices.get(device.id)
                    if existing is None:
                        self.devices[device.id] = device
                        self._start_poll(device)
                    else:
                        existing.name = device.name
                        existing.model = device.model
                        existing.manufacturer = device.manufacturer
                        existing.capabilities = device.capabilities
                        existing.meta.update(device.meta)
                        if device.poll_interval is not None:
                            existing.poll_interval = device.poll_interval
            for device_id, device in list(self.devices.items()):
                if device.provider in failed:
                    continue
                if device_id not in seen:
                    _LOGGER.info("device %s disappeared; stopping poll", device_id)
                    self._stop_poll(device_id)
                    del self.devices[device_id]

    async def _list_provider(self, provider: Provider) -> list[Device]:
        return await asyncio.wait_for(provider.list_devices(), timeout=30)

    # ---- listeners -----------------------------------------------------
    def add_listener(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: StateListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify(self, device: Device, changed: set[Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(device, changed)
            except Exception:
                _LOGGER.exception("state listener failed")

    # ---- polling -------------------------------------------------------
    def _interval(self, device: Device) -> float:
        if device.poll_interval:
            return device.poll_interval
        return self.config.gateway.poll_intervals.get(device.provider, 15.0)

    def _start_poll(self, device: Device) -> None:
        if device.id in self._poll_tasks or self._stopping:
            return
        self._poll_tasks[device.id] = asyncio.create_task(
            self._poll_loop(device), name=f"poll:{device.id}"
        )

    def _stop_poll(self, device_id: str) -> None:
        task = self._poll_tasks.pop(device_id, None)
        if task:
            task.cancel()

    async def _poll_loop(self, device: Device) -> None:
        interval = self._interval(device)
        delay = medium = interval
        while not self._stopping:
            provider = self.providers.get(device.provider)
            if provider is None:
                return
            try:
                values = await provider.refresh(device)
                changed = device.apply(values)
                if not device.online:
                    device.online = True
                    changed = changed | {None}
                device.error = None
                device.last_seen = time.time()
                delay = medium
                if changed:
                    self._notify(device, changed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                device.online = False
                device.error = str(exc)[:300]
                delay = min(max(delay * 1.8, interval), 300.0)
                _LOGGER.warning("poll %s failed: %s (retry in %.0fs)", device.id, exc, delay)
                self._notify(device, set())
            await asyncio.sleep(delay)

    # ---- commands ------------------------------------------------------
    async def set_capability(self, device_id: str, capability: Any, value: Any) -> None:
        device = self.devices.get(device_id)
        if device is None:
            raise KeyError(f"unknown device {device_id}")
        if capability not in device.capabilities:
            raise ValueError(f"{device_id} does not support {capability}")
        provider = self.providers[device.provider]
        lock = self._command_locks.setdefault(device_id, asyncio.Lock())
        previous = device.state.get(capability)
        # Optimistic update keeps the Home app responsive while the cloud
        # round trip completes; a failure restores the old value.
        if device.state.get(capability) != value:
            device.state[capability] = value
            self._notify(device, {capability})
        try:
            async with lock:
                await provider.execute(device, Command(capability, value))
            await asyncio.sleep(0.6)
            values = await provider.refresh(device)
            changed = device.apply(values)
            device.online = True
            device.error = None
            device.last_seen = time.time()
            if changed:
                self._notify(device, changed)
        except Exception as exc:
            _LOGGER.warning("command %s=%r on %s failed: %s", capability, value, device_id, exc)
            device.state[capability] = previous
            device.error = str(exc)[:300]
            self._notify(device, {capability})
            raise

    async def refresh_device(self, device_id: str) -> None:
        device = self.devices[device_id]
        provider = self.providers[device.provider]
        values = await provider.refresh(device)
        changed = device.apply(values)
        device.online = True
        device.error = None
        device.last_seen = time.time()
        if changed:
            self._notify(device, changed)

    # ---- views ---------------------------------------------------------
    def all_devices(self) -> list[Device]:
        return sorted(self.devices.values(), key=lambda d: (d.provider, d.name))

    def get(self, device_id: str) -> Device | None:
        return self.devices.get(device_id)

    def snapshots(self) -> list[dict[str, Any]]:
        return [device.snapshot() for device in self.all_devices()]
