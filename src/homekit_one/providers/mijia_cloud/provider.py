"""Mijia cloud provider for all devices visible to a Xiaomi account.

This is the path for Zigbee / BLE Mesh sub-devices behind a central hub: they
have a cloud ``did`` and a MIoT-Spec URN, but no IP or LAN token.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homekit_one.config import MijiaCloudConfig
from homekit_one.core.model import Capability, Command, Device
from homekit_one.providers.base import Provider, ProviderError
from homekit_one.providers.mijia_cloud.client import (
    MijiaCloudAuthError,
    MijiaCloudError,
    XiaomiCloudClient,
)
from homekit_one.providers.mijia_cloud.oauth import XiaomiOAuthClient
from homekit_one.providers.mijia_cloud.spec import CloudSpec, build_cloud_spec
from homekit_one.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)

PROP_BATCH = 15


class MijiaCloudProvider(Provider):
    name = "mijia_cloud"

    def __init__(
        self, config: MijiaCloudConfig, secrets: SecretsStore, state_dir: Path
    ) -> None:
        super().__init__(config, secrets, state_dir)
        username = self.secrets.resolve(
            "mijia_cloud",
            "username",
            config.username,
            "HOMEKIT_ONE_MIJIA_CLOUD_USERNAME",
        )
        password = self.secrets.resolve(
            "mijia_cloud",
            "password",
            config.password,
            "HOMEKIT_ONE_MIJIA_CLOUD_PASSWORD",
        )
        country = str(
            secrets.get("mijia_cloud", "country") or config.country or "cn"
        ).lower()
        oauth_tokens = self.secrets.section("mijia_oauth")
        self.client: XiaomiCloudClient | XiaomiOAuthClient | None = None
        if oauth_tokens.get("access_token") or oauth_tokens.get("refresh_token"):
            self.client = XiaomiOAuthClient(
                country,
                state_dir,
                tokens=oauth_tokens,
                on_tokens=lambda values: self.secrets.set_many(
                    "mijia_oauth", values
                ),
            )
        elif username and password:
            self.client = XiaomiCloudClient(
                str(username), str(password), country, state_dir
            )
        self._specs: dict[str, CloudSpec] = {}
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def start(self) -> None:
        if self.client is None:
            _LOGGER.warning(
                "米家云 provider 未配置授权：请运行 homekit-one mijia-oauth-login，"
                "或填写账号密码"
            )
            return
        try:
            await self.client.login()
            self._last_error = None
        except MijiaCloudAuthError as exc:
            self._last_error = str(exc)
            _LOGGER.warning("米家云登录失败: %s", exc)
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            _LOGGER.warning("米家云初始化失败: %s", exc)

    async def list_devices(self) -> list[Device]:
        if self.client is None:
            return []
        try:
            records = await self.client.get_device_records()
        except MijiaCloudError as exc:
            self._last_error = str(exc)
            raise ProviderError(f"米家云设备列表失败: {exc}") from exc
        self._last_error = None

        allow = set(self.config.devices)
        devices: list[Device] = []
        for record in records:
            did = str(record.get("did") or "")
            model = str(record.get("model") or "")
            urn = str(
                record.get("spec_type")
                or record.get("specType")
                or record.get("urn")
                or ""
            )
            if not did or not urn:
                _LOGGER.info("米家云设备缺少 did/spec_type，已跳过: %s", record)
                continue
            if allow and did not in allow:
                continue
            try:
                spec_data = await self.client.get_spec(urn)
                spec = build_cloud_spec(spec_data, urn, model)
            except MijiaCloudError as exc:
                _LOGGER.warning("米家云设备 %s 的 spec 获取失败: %s", did, exc)
                continue
            if spec is None:
                _LOGGER.info(
                    "米家云设备 %s (%s / %s) 暂不支持，已跳过",
                    record.get("name") or did,
                    model,
                    urn,
                )
                continue
            device = Device(
                id=f"mijia_cloud:{did}",
                name=str(record.get("name") or did),
                provider=self.name,
                kind=spec.kind,
                model=model,
                manufacturer=_manufacturer(model),
                capabilities=set(spec.capabilities),
                meta={
                    "did": did,
                    "urn": urn,
                    "model": model,
                    "parent_id": record.get("parent_id")
                    or record.get("parentId"),
                    "target_temp_range": spec.target_temp_range,
                },
                online=_online(record),
                poll_interval=self.config.poll_interval,
            )
            self._specs[device.id] = spec
            devices.append(device)
        return devices

    async def refresh(self, device: Device) -> dict[Capability, Any]:
        if self.client is None:
            raise ProviderError("米家云账号未配置")
        spec = self._specs.get(device.id)
        if spec is None:
            spec = await self._load_spec(device)
        did = str(device.meta["did"])
        try:
            raw = await self._read_props(did, spec)
        except MijiaCloudError as exc:
            raise ProviderError(f"{device.name} 云端读取失败: {exc}") from exc
        if not raw:
            raise ProviderError(f"{device.name} 没有返回可读属性")
        return spec.decode(raw)

    async def execute(self, device: Device, command: Command) -> None:
        if self.client is None:
            raise ProviderError("米家云账号未配置")
        spec = self._specs.get(device.id) or await self._load_spec(device)
        did = str(device.meta["did"])
        action = spec.action_for(command)
        if action is not None:
            try:
                result = await self.client.call_action(
                    did, action.siid, action.aiid, list(action.in_params)
                )
            except MijiaCloudError as exc:
                raise ProviderError(f"{device.name} 云端控制失败: {exc}") from exc
            if isinstance(result, dict) and int(result.get("code", 0)) != 0:
                raise ProviderError(
                    f"{device.name} 云端控制失败: code={result.get('code')} {result}"
                )
            await asyncio.sleep(0.5)
            return
        if command.capability is Capability.POWER and spec.actions:
            # Momentary devices (pet feeder) have no "off" action; HomeKit may
            # still send one after the state springs back.
            return
        writes = spec.encode(command)
        if not writes:
            raise ProviderError(f"{device.name} 不支持 {command.capability}")
        params = [
            {"did": did, "siid": siid, "piid": piid, "value": value}
            for siid, piid, value in writes
        ]
        try:
            result = await self.client.set_props(params)
        except MijiaCloudError as exc:
            raise ProviderError(f"{device.name} 云端控制失败: {exc}") from exc
        for item in result or []:
            if int(item.get("code", 0)) != 0:
                raise ProviderError(
                    f"{device.name} 云端控制失败: code={item.get('code')} {item}"
                )
        # The cloud reports property changes asynchronously. Wait briefly so
        # the manager's immediate refresh does not read the old value and
        # roll the optimistic HomeKit update back.
        try:
            for _ in range(5):
                await asyncio.sleep(0.3)
                raw = await self._read_props(did, spec)
                if all(raw.get((siid, piid)) == value for siid, piid, value in writes):
                    break
        except MijiaCloudError:
            pass

    async def _read_props(
        self, did: str, spec: CloudSpec
    ) -> dict[tuple[int, int], Any]:
        assert self.client is not None
        params = [
            {"did": did, "siid": siid, "piid": piid}
            for siid, piid in spec.properties()
        ]
        raw: dict[tuple[int, int], Any] = {}
        for start in range(0, len(params), PROP_BATCH):
            chunk = params[start : start + PROP_BATCH]
            for item in await self.client.get_props(chunk):
                if "value" not in item:
                    continue
                try:
                    key = (int(item["siid"]), int(item["piid"]))
                except (KeyError, TypeError, ValueError):
                    continue
                raw[key] = item["value"]
        return raw

    async def _load_spec(self, device: Device) -> CloudSpec:
        if self.client is None:
            raise ProviderError("米家云账号未配置")
        urn = str(device.meta.get("urn") or "")
        if not urn:
            raise ProviderError(f"{device.name} 缺少 MIoT-Spec URN")
        try:
            data = await self.client.get_spec(urn)
        except MijiaCloudError as exc:
            raise ProviderError(f"{device.name} 的 spec 获取失败: {exc}") from exc
        spec = build_cloud_spec(data, urn, device.model)
        if spec is None:
            raise ProviderError(f"{device.name} 的 spec 无法映射")
        self._specs[device.id] = spec
        return spec

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.close()


def _online(record: dict[str, Any]) -> bool:
    value = record.get("isOnline", record.get("online", record.get("is_online", True)))
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "offline")
    return bool(value)


def _manufacturer(model: str) -> str:
    prefix = model.split(".", 1)[0] if model else ""
    return prefix or "Xiaomi"
