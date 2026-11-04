"""TCL+ (China) TV provider."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homeone.config import TclPlusConfig
from homeone.core.model import Capability, Command, Device, DeviceKind
from homeone.providers.base import Provider, ProviderError
from homeone.providers.tcl_plus.client import (
    TclPlusAuthError,
    TclPlusClient,
    TclPlusError,
)
from homeone.providers.tcl_plus.mapping import (
    decode_tv_status,
    encode_tv_command,
    is_tv,
    parse_sources,
)
from homeone.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)


class TclPlusProvider(Provider):
    name = "tcl_plus"

    def __init__(
        self, config: TclPlusConfig, secrets: SecretsStore, state_dir: Path
    ) -> None:
        super().__init__(config, secrets, state_dir)
        phone = config.phone or secrets.get("tcl_plus", "phone")
        password = config.password or secrets.get("tcl_plus", "password")
        account_id = config.account_id or secrets.get("tcl_plus", "account_id")
        refresh_token = config.refresh_token or secrets.get(
            "tcl_plus", "refresh_token"
        )
        self.client: TclPlusClient | None = None
        if (phone and password) or (account_id and refresh_token):
            self.client = TclPlusClient(
                phone=str(phone or ""),
                password=str(password or ""),
                account_id=str(account_id or ""),
                refresh_token=str(refresh_token or ""),
                state_dir=state_dir,
            )
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if self.client is None:
            _LOGGER.warning(
                "TCL+ provider 未配置凭据：运行 homeone tcl-plus-list -u 手机号 --save"
            )
            return
        try:
            await self.client.login()
            self._last_error = None
        except TclPlusAuthError as exc:
            self._last_error = str(exc)
            _LOGGER.warning("TCL+ 登录失败: %s", exc)
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            _LOGGER.warning("TCL+ 初始化失败: %s", exc)

    async def list_devices(self) -> list[Device]:
        if self.client is None:
            return []
        try:
            raw_devices = await self.client.get_devices()
        except TclPlusError as exc:
            self._last_error = str(exc)
            raise ProviderError(f"TCL+ 设备列表失败: {exc}") from exc
        self._last_error = None

        allow = set(self.config.devices)
        devices: list[Device] = []
        for raw in raw_devices:
            device_id = str(raw.get("deviceId") or "")
            if not device_id or (allow and device_id not in allow):
                continue
            if not is_tv(raw.get("category")):
                _LOGGER.info(
                    "TCL+ 设备 %s (%s) 暂不支持，跳过",
                    raw.get("nickName") or device_id,
                    raw.get("category"),
                )
                continue
            try:
                status = await self.client.get_snapshot(device_id)
            except TclPlusError as exc:
                _LOGGER.warning("TCL+ 设备 %s 状态读取失败: %s", device_id, exc)
                status = {}
            sources = parse_sources(status)
            capabilities = {Capability.POWER}
            if "volume" in status:
                capabilities.add(Capability.VOLUME)
            if "mute" in status:
                capabilities.add(Capability.MUTE)
            if sources:
                capabilities.add(Capability.INPUT)
            wechat_control = str(raw.get("weChatControl", "")).strip()
            if wechat_control == "0":
                _LOGGER.warning(
                    "TCL+ 电视 %s 的 weChatControl=0，云端控制可能不可用；"
                    "请确认电视已开机并重新运行 tcl-plus-list",
                    raw.get("nickName") or device_id,
                )
            device = Device(
                id=f"tcl_plus:{device_id}",
                name=str(raw.get("nickName") or raw.get("deviceName") or device_id),
                provider=self.name,
                kind=DeviceKind.TV,
                model=str(raw.get("productKey") or ""),
                manufacturer="TCL",
                capabilities=capabilities,
                meta={
                    "device_id": device_id,
                    "product_key": raw.get("productKey"),
                    "sources": sources,
                    "wechat_control": wechat_control,
                    "online": raw.get("isOnline"),
                },
                online=_online(raw.get("isOnline")),
                poll_interval=self.config.poll_interval,
            )
            devices.append(device)
        return devices

    async def refresh(self, device: Device) -> dict[Capability, Any]:
        if self.client is None:
            raise ProviderError("TCL+ 凭据未配置")
        device_id = str(device.meta["device_id"])
        try:
            raw_devices = await self.client.get_devices()
        except TclPlusError as exc:
            raise ProviderError(f"{device.name} 在线状态读取失败: {exc}") from exc
        raw = next(
            (
                item
                for item in raw_devices
                if str(item.get("deviceId")) == device_id
            ),
            None,
        )
        if raw is None:
            raise ProviderError(f"{device.name} 不在 TCL+ 账号设备列表里")
        if not _online(raw.get("isOnline")):
            device.meta["online"] = raw.get("isOnline")
            device.meta["wechat_control"] = str(raw.get("weChatControl", ""))
            raise ProviderError(f"{device.name} 离线")
        device.meta["online"] = raw.get("isOnline")
        device.meta["wechat_control"] = str(raw.get("weChatControl", ""))
        try:
            status = await self.client.get_snapshot(device_id)
        except TclPlusError as exc:
            raise ProviderError(f"{device.name} 状态读取失败: {exc}") from exc
        if not status:
            raise ProviderError(f"{device.name} 没有返回状态")
        values = decode_tv_status(status)
        if not values:
            raise ProviderError(f"{device.name} 状态里没有可识别字段")
        return values

    async def execute(self, device: Device, command: Command) -> None:
        if self.client is None:
            raise ProviderError("TCL+ 凭据未配置")
        attributes = encode_tv_command(command)
        if not attributes:
            raise ProviderError(f"{device.name} 不支持 {command.capability}")
        try:
            await self.client.send_command(
                str(device.meta["device_id"]), attributes
            )
        except TclPlusError as exc:
            raise ProviderError(f"{device.name} 控制失败: {exc}") from exc
        await asyncio.sleep(0.5)

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.close()


def _online(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "offline")
    return bool(value)
