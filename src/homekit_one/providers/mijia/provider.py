"""Mijia LAN provider."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from homekit_one.config import MijiaConfig, MijiaDeviceConfig
from homekit_one.core.model import Capability, Command, Device
from homekit_one.providers.base import Provider, ProviderError
from homekit_one.providers.mijia import crypto
from homekit_one.providers.mijia.client import MiioClient
from homekit_one.providers.mijia.models import MODELS, ModelSpec
from homekit_one.registry import DeviceRegistry
from homekit_one.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)


class MijiaProvider(Provider):
    name = "mijia"

    def __init__(
        self,
        config: MijiaConfig,
        secrets: SecretsStore,
        state_dir: Path,
        registry: DeviceRegistry,
    ) -> None:
        super().__init__(config, secrets, state_dir)
        self.registry = registry
        self._clients: dict[str, MiioClient] = {}
        self._specs: dict[str, ModelSpec] = {}

    # ---- discovery -----------------------------------------------------
    def _merged_entries(self) -> list[dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        for item in self.config.devices:
            entries[str(item.id)] = _entry_from_config(item)
        for item in self.registry.mijia_entries():
            key = str(item["id"])
            entries[key] = {**entries.get(key, {}), **item}
        return list(entries.values())

    def _token(self, device_id: str, configured: str | None) -> str | None:
        keys = [str(device_id)]
        if str(device_id).lower().startswith("0x"):
            try:
                keys.append(str(int(str(device_id), 16)))
            except ValueError:
                pass
        # Documented precedence: environment > secrets.json > config.toml.
        for key in keys:
            suffix = re.sub(r"[^A-Za-z0-9]", "_", key).upper()
            for prefix in ("HOMEKIT_ONE_MIJIA_TOKEN_", "HONE_MIJIA_TOKEN_"):
                if os.environ.get(f"{prefix}{suffix}"):
                    return os.environ[f"{prefix}{suffix}"]
        tokens = self.secrets.get("mijia", "tokens", default={}) or {}
        for key in keys:
            if tokens.get(key):
                return str(tokens[key])
        if configured:
            return configured
        return None

    async def list_devices(self) -> list[Device]:
        devices: list[Device] = []
        skipped_models: set[str] = set()
        for entry in self._merged_entries():
            model = str(entry.get("model") or "")
            spec = MODELS.get(model)
            if spec is None:
                if model:
                    skipped_models.add(model)
                continue
            token = self._token(str(entry["id"]), entry.get("token"))
            host = str(entry.get("host") or "")
            if not token or not host:
                _LOGGER.warning(
                    "米家设备 %s 缺少 token 或 IP，已跳过（可用 homekit-one mijia-import 导入）",
                    entry.get("id"),
                )
                continue
            device = Device(
                id=f"mijia:{entry['id']}",
                name=str(entry.get("name") or entry["id"]),
                provider=self.name,
                kind=spec.kind,
                model=model,
                manufacturer=spec.manufacturer,
                capabilities=spec.capabilities,
                meta={
                    "host": host,
                    "token": token,
                    "raw_id": str(entry["id"]),
                    "did": str(entry["id"]),
                },
                poll_interval=self.config.poll_interval,
                online=False,
            )
            self._specs[device.id] = spec
            devices.append(device)
        if skipped_models:
            _LOGGER.info(
                "米家局域网跳过 %d 个暂无本地映射的型号（如已启用米家云会由云端接入）：%s",
                len(skipped_models),
                ", ".join(sorted(skipped_models)),
            )
        return devices

    # ---- state ---------------------------------------------------------
    def _client(self, device: Device) -> MiioClient:
        host = str(device.meta["host"])
        token = str(device.meta["token"])
        cached = self._clients.get(device.id)
        if cached is None or cached.host != host or cached.token.hex() != crypto.token_bytes(token).hex():
            cached = MiioClient(host=host, token=token)
            self._clients[device.id] = cached
        return cached

    def _spec(self, device: Device) -> ModelSpec:
        spec = self._specs.get(device.id) or MODELS.get(device.model)
        if spec is None:
            raise ProviderError(f"未知米家型号: {device.model}")
        return spec

    async def refresh(self, device: Device) -> dict[Capability, Any]:
        spec = self._spec(device)
        client = self._client(device)
        try:
            raw = await client.get_properties(spec.properties())
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"{device.name} 读取失败: {exc}") from exc
        if not raw:
            raise ProviderError(f"{device.name} 没有返回任何属性")
        return spec.decode(raw)

    async def execute(self, device: Device, command: Command) -> None:
        spec = self._spec(device)
        writes = spec.encode(command)
        if not writes:
            raise ProviderError(f"{device.model} 不支持 {command.capability}")
        client = self._client(device)
        try:
            await client.set_properties(writes)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"{device.name} 控制失败: {exc}") from exc

    async def stop(self) -> None:
        self._clients.clear()


def _entry_from_config(item: MijiaDeviceConfig) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "model": item.model,
        "host": item.host,
        "token": item.token,
    }
