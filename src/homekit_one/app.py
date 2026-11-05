"""Process orchestration: providers -> manager -> HomeKit + web UI."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

import uvicorn

from homekit_one.config import Config
from homekit_one.core.manager import DeviceManager
from homekit_one.homekit.bridge import HomeKitBridge
from homekit_one.providers.base import Provider
from homekit_one.providers.fake import FakeProvider
from homekit_one.providers.plugins import build_providers
from homekit_one.registry import DeviceRegistry
from homekit_one.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)


class Gateway:
    def __init__(self, config: Config, *, no_homekit: bool = False) -> None:
        self.config = config
        self.no_homekit = no_homekit
        self.state_dir = config.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass
        self.secrets = SecretsStore(self.state_dir / "secrets.json")
        self.registry = DeviceRegistry(self.state_dir / "devices.json")
        self.providers = self._build_providers()
        self.manager = DeviceManager(config, self.providers)
        # Setup UI state: last verification report per provider, LAN discovery
        # results, and whether the running process is stale. These are
        # deliberately in-memory; a restart clears them with the old process.
        self.setup_reports: dict[str, Any] = {}
        self.mijia_discovery: list[dict[str, Any]] | None = None
        self.setup_dirty = False
        self.bridge: HomeKitBridge | None = None
        self.web_server: uvicorn.Server | None = None
        self._web_task: asyncio.Task[Any] | None = None
        self._stop = asyncio.Event()

    def _build_providers(self) -> list[Provider]:
        providers: list[Provider] = []
        if self.config.fake.enabled:
            providers.append(FakeProvider(self.config.fake, self.secrets, self.state_dir))
        providers.extend(
            build_providers(
                self.config, self.secrets, self.state_dir, self.registry
            )
        )
        if not providers:
            providers.append(FakeProvider(self.config.fake, self.secrets, self.state_dir))
        return providers

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        await self.manager.start()
        if not self.no_homekit:
            self.bridge = HomeKitBridge(self.config, self.manager)
            await self.bridge.start()
        from homekit_one.web.server import create_app

        app = create_app(self)
        uvicorn_config = uvicorn.Config(
            app,
            host=self.config.gateway.web_host,
            port=self.config.gateway.web_port,
            log_level=self.config.gateway.log_level.lower(),
            access_log=False,
            loop="none",
        )
        self.web_server = uvicorn.Server(uvicorn_config)
        self._web_task = asyncio.create_task(self.web_server.serve(), name="web")
        self._web_task.add_done_callback(self._web_done)
        _LOGGER.info(
            "web UI at http://%s:%s",
            self.config.gateway.web_host,
            self.config.gateway.web_port,
        )

    def _web_done(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.error("web server stopped: %s", exc)
            self.request_stop()

    async def serve(self) -> None:
        await self.start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except NotImplementedError:
                pass
        await self._stop.wait()
        await self.stop()

    async def stop(self) -> None:
        if self.web_server is not None:
            self.web_server.should_exit = True
        if self._web_task is not None:
            try:
                await asyncio.wait_for(self._web_task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self._web_task.cancel()
            except Exception:
                _LOGGER.exception("web server shutdown failed")
        if self.bridge is not None:
            await self.bridge.stop()
        await self.manager.stop()
        _LOGGER.info("gateway stopped")

    def request_stop(self) -> None:
        self._stop.set()

    def mark_setup_changed(self) -> None:
        """Remember that credentials/devices changed but the process is stale."""
        self.setup_dirty = True

    def request_restart(self) -> None:
        """Exit so launchd (KeepAlive) starts a fresh process."""
        loop = asyncio.get_running_loop()
        loop.call_later(0.5, self.request_stop)

    # ---- views ---------------------------------------------------------
    def provider_status(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for device in self.manager.devices.values():
            counts[device.provider] = counts.get(device.provider, 0) + 1
        status = []
        for provider in self.providers:
            status.append(
                {
                    "name": provider.name,
                    "devices": counts.get(provider.name, 0),
                    "last_error": getattr(provider, "last_error", None),
                }
            )
        return status

    def pairing_info(self) -> dict[str, Any] | None:
        return self.bridge.pairing_info() if self.bridge else None
