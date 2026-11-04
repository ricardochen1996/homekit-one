"""TCL Home cloud provider (split AC first release)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homeone.config import TclConfig
from homeone.core.model import Capability, Command, Device, DeviceKind
from homeone.providers.base import Provider, ProviderError
from homeone.providers.tcl.cloud import TclCloud, TclError
from homeone.providers.tcl.mapping import decode_shadow, encode_command, is_ac_device
from homeone.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)

AC_CAPABILITIES = {
    Capability.POWER,
    Capability.MODE,
    Capability.TARGET_TEMP,
    Capability.CURRENT_TEMP,
    Capability.FAN_SPEED,
    Capability.SWING,
}


class TclProvider(Provider):
    name = "tcl"

    def __init__(self, config: TclConfig, secrets: SecretsStore, state_dir: Path) -> None:
        super().__init__(config, secrets, state_dir)
        username = config.username or secrets.get("tcl", "username")
        password = config.password or secrets.get("tcl", "password")
        self.cloud: TclCloud | None = None
        if username and password:
            self.cloud = TclCloud(str(username), str(password), state_dir)
        self._last_error: str | None = None
        # One TCL account shares a token/session cache; serialize cloud calls
        # so a token refresh cannot race a shadow read from another device.
        self._lock = asyncio.Lock()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if self.cloud is None:
            _LOGGER.warning("TCL provider 未配置账号密码")
            return
        try:
            await self.cloud.ensure_session()
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            _LOGGER.warning("TCL 会话初始化失败: %s", exc)

    async def list_devices(self) -> list[Device]:
        if self.cloud is None:
            return []
        try:
            things = await self.cloud.get_things()
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            raise ProviderError(f"TCL 设备列表获取失败: {exc}") from exc

        allow = set(self.config.devices)
        devices: list[Device] = []
        for thing in things:
            device_id = str(thing.get("device_id") or thing.get("deviceId") or "")
            device_type = str(thing.get("device_name") or thing.get("deviceName") or "")
            if not device_id or not is_ac_device(device_type):
                continue
            if allow and device_id not in allow:
                continue
            name = (
                thing.get("nick_name")
                or thing.get("nickName")
                or thing.get("room")
                or f"TCL {device_type}"
            )
            online = str(thing.get("is_online", thing.get("isOnline", 1))) not in ("0", "False", "false")
            devices.append(
                Device(
                    id=f"tcl:{device_id}",
                    name=str(name),
                    provider=self.name,
                    kind=DeviceKind.AC,
                    model=device_type,
                    manufacturer="TCL",
                    capabilities=set(AC_CAPABILITIES),
                    meta={
                        "device_id": device_id,
                        "device_type": device_type,
                        "product_key": thing.get("product_key") or thing.get("productKey") or "",
                    },
                    online=online,
                    poll_interval=self.config.poll_interval,
                )
            )
        return devices

    async def refresh(self, device: Device) -> dict[Capability, Any]:
        if self.cloud is None:
            raise ProviderError("TCL 账号未配置")
        try:
            async with self._lock:
                shadow = await self.cloud.get_shadow(str(device.meta["device_id"]))
        except TclError as exc:
            raise ProviderError(f"{device.name} shadow 读取失败: {exc}") from exc
        state = shadow.get("state") or {}
        reported = state.get("reported") or {}
        delta = state.get("delta") or {}
        raw = {**reported, **delta}
        if not raw:
            raise ProviderError(f"{device.name} shadow 为空")
        return decode_shadow(raw, self.config.work_modes)

    async def execute(self, device: Device, command: Command) -> None:
        if self.cloud is None:
            raise ProviderError("TCL 账号未配置")
        desired = encode_command(command, self.config.work_modes)
        if not desired:
            raise ProviderError(f"TCL 不支持 {command.capability}")
        device_id = str(device.meta["device_id"])
        async with self._lock:
            try:
                await self.cloud.set_desired(device_id, desired)
            except TclError as exc:
                raise ProviderError(f"{device.name} 控制失败: {exc}") from exc
            # The shadow is eventually consistent. Waiting is best-effort: a
            # failed read here must not turn a successful write into an error.
            try:
                for _ in range(6):
                    await asyncio.sleep(0.3)
                    shadow = await self.cloud.get_shadow(device_id)
                    state = shadow.get("state") or {}
                    raw = {**(state.get("reported") or {}), **(state.get("delta") or {})}
                    if all(raw.get(key) == value for key, value in desired.items()):
                        break
            except TclError:
                pass

    async def stop(self) -> None:
        if self.cloud is not None:
            await self.cloud.close()
