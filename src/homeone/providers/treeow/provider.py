"""Treeow (树新风) purifier provider."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from homeone.config import TreeowConfig
from homeone.core.model import Capability, Command, Device, DeviceKind
from homeone.providers.base import Provider, ProviderError
from homeone.providers.treeow.api import TreeowAPI, TreeowError
from homeone.providers.treeow.mapping import decode_props, encode_command
from homeone.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)

PURIFIER_CAPABILITIES = {
    Capability.POWER,
    Capability.MODE,
    Capability.FAN_SPEED,
    Capability.PM25,
    Capability.TEMPERATURE,
    Capability.HUMIDITY,
    Capability.FILTER_LIFE,
    Capability.CHILD_LOCK,
    Capability.DISPLAY,
}


class TreeowProvider(Provider):
    name = "treeow"

    def __init__(self, config: TreeowConfig, secrets: SecretsStore, state_dir: Path) -> None:
        super().__init__(config, secrets, state_dir)
        account = config.account or secrets.get("treeow", "account")
        password = config.password or secrets.get("treeow", "password")
        self.api: TreeowAPI | None = None
        if account and password:
            self.api = TreeowAPI(str(account), str(password))
        self._devices: list[dict[str, Any]] = []
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if self.api is None:
            _LOGGER.warning("Treeow provider 未配置账号密码")
            return
        try:
            await self.api.login()
            await self._discover()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            _LOGGER.warning("树新风初始化失败: %s", exc)

    async def _discover(self) -> None:
        assert self.api is not None
        found: list[dict[str, Any]] = []
        for home in await self.api.get_homes():
            for group in home.get("homeGroups") or []:
                group_id = str(group.get("id") or "") if isinstance(group, dict) else ""
                if not group_id:
                    continue
                for item in await self.api.get_device_list(group_id):
                    if item.get("deviceSerial"):
                        found.append({**item, "_group_id": group_id})
        self._devices = found
        _LOGGER.info("树新风账号下有 %d 台设备", len(found))

    async def list_devices(self) -> list[Device]:
        if self.api is None:
            return []
        if not self._devices:
            try:
                await self.api.login()
                await self._discover()
                self._last_error = None
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                raise ProviderError(f"树新风设备列表获取失败: {exc}") from exc
        allow = set(self.config.devices)
        devices: list[Device] = []
        for item in self._devices:
            serial = str(item.get("deviceSerial"))
            if allow and serial not in allow:
                continue
            name = (
                item.get("deviceName")
                or item.get("nickName")
                or item.get("name")
                or f"树新风 {serial}"
            )
            model = str(item.get("model") or item.get("productName") or "Treeow")
            devices.append(
                Device(
                    id=f"treeow:{serial}",
                    name=str(name),
                    provider=self.name,
                    kind=DeviceKind.PURIFIER,
                    model=model,
                    manufacturer="Treeow",
                    capabilities=set(PURIFIER_CAPABILITIES),
                    meta={
                        "serial": serial,
                        "group_id": str(item.get("_group_id") or ""),
                    },
                    poll_interval=self.config.poll_interval,
                    online=False,
                )
            )
        return devices

    async def refresh(self, device: Device) -> dict[Capability, Any]:
        if self.api is None:
            raise ProviderError("树新风账号未配置")
        try:
            info = await self.api.get_device_info(str(device.meta["serial"]))
        except TreeowError as exc:
            raise ProviderError(f"{device.name} 读取失败: {exc}") from exc
        props = _extract_props(info)
        if not props:
            raise ProviderError(f"{device.name} 没有返回属性")
        online = props.get("online_state")
        if online is not None and str(online).strip().lower() in ("0", "false", "offline"):
            raise ProviderError(f"{device.name} 云端标记为离线")
        return decode_props(props)

    async def execute(self, device: Device, command: Command) -> None:
        if self.api is None:
            raise ProviderError("树新风账号未配置")
        pairs = encode_command(command)
        if not pairs:
            raise ProviderError(f"树新风不支持 {command.capability}")
        try:
            for prop, value in pairs:
                await self.api.control(str(device.meta["serial"]), prop, value)
            await asyncio.sleep(0.4)
        except TreeowError as exc:
            raise ProviderError(f"{device.name} 控制失败: {exc}") from exc

    async def stop(self) -> None:
        if self.api is not None:
            await self.api.close()


def _extract_props(info: dict[str, Any]) -> dict[str, Any]:
    entries = info.get("props") or []
    if not isinstance(entries, list) or not entries:
        return {}
    raw = entries[0].get("value") if isinstance(entries[0], dict) else None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        return {}
    air = parsed.get("Air_Purifier") if isinstance(parsed, dict) else None
    if not isinstance(air, dict):
        return {}
    return {key: value for key, value in air.items() if not key.endswith("&timestamp")}
