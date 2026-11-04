"""Xiaomi cloud clients for Mijia devices, including hub sub-devices.

Two transports share the same device/property/spec logic:

* ``XiaomiCloudClient``: legacy account-password login through ``micloud``.
  Xiaomi now rejects many of these logins with HTTP 403, so it is only a
  fallback.
* ``XiaomiOAuthClient`` (``oauth.py``): the OAuth 2.0 flow used by the official
  Xiaomi Home integration. This is the recommended path and works with
  two-factor accounts because the browser handles verification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

SPEC_URL = "https://miot-spec.org/miot-spec-v2/instance"
DEVICE_BATCH = 150
PROP_BATCH = 15


class MijiaCloudError(RuntimeError):
    pass


class MijiaCloudAuthError(MijiaCloudError):
    pass


class BaseCloudClient:
    """Device/property/spec plumbing shared by both login transports."""

    def __init__(self, country: str, state_dir: Path | None = None) -> None:
        self.country = (country or "cn").lower()
        self.state_dir = state_dir
        self._http = httpx.AsyncClient(timeout=25, follow_redirects=True)
        self._specs: dict[str, dict[str, Any]] = {}
        self._spec_cache = state_dir / "miot_specs" if state_dir else None

    @property
    def api_base(self) -> str:
        if self.country == "cn":
            return "https://api.io.mi.com/app"
        return f"https://{self.country}.api.io.mi.com/app"

    async def request(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def _fallback_dids(self) -> list[str]:
        return []

    # ---- devices -------------------------------------------------------
    async def get_home_dids(self) -> list[str]:
        result = (
            await self.request(
                "/v2/homeroom/gethome",
                {
                    "limit": 150,
                    "fetch_share": True,
                    "fetch_share_dev": True,
                    "plat_form": 0,
                    "app_ver": 9,
                },
            )
        ).get("result") or {}
        dids: list[str] = []

        def collect(payload: dict[str, Any]) -> None:
            for key in ("homelist", "share_home_list"):
                for home in payload.get(key) or []:
                    dids.extend(_strings(home.get("dids")))
                    for room in home.get("roomlist") or []:
                        dids.extend(_strings(room.get("dids")))

        collect(result)
        while result.get("has_more") and result.get("max_id"):
            more = (
                await self.request(
                    "/v2/homeroom/get_dev_room_page",
                    {"start_id": str(result["max_id"]), "limit": 150},
                )
            ).get("result") or {}
            for home in more.get("info") or []:
                dids.extend(_strings(home.get("dids")))
                for room in home.get("roomlist") or []:
                    dids.extend(_strings(room.get("dids")))
            result = more
        return _unique(dids)

    async def get_device_records(self) -> list[dict[str, Any]]:
        dids = await self.get_home_dids()
        if not dids:
            dids = await self._fallback_dids()
        records: dict[str, dict[str, Any]] = {}
        for batch in _chunks(dids, DEVICE_BATCH):
            await self._device_page(batch, records)
        if not records:
            # Some accounts return the full list only when dids is empty.
            await self._device_page([], records)
        return sorted(
            records.values(),
            key=lambda item: (str(item.get("name") or ""), str(item.get("did") or "")),
        )

    async def _device_page(
        self, dids: list[str], records: dict[str, dict[str, Any]]
    ) -> None:
        start_did: str | None = None
        while True:
            data: dict[str, Any] = {
                "limit": 200,
                "get_split_device": True,
                "get_third_device": True,
                "dids": dids,
            }
            if start_did:
                data["start_did"] = start_did
            result = (await self.request("/v2/home/device_list_page", data)).get(
                "result"
            ) or {}
            for item in result.get("list") or []:
                did = item.get("did")
                if did:
                    records[str(did)] = item
            next_start = result.get("next_start_did")
            if (
                not result.get("has_more")
                or not next_start
                or str(next_start) == start_did
            ):
                break
            start_did = str(next_start)

    # ---- MIoT properties ----------------------------------------------
    async def get_props(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = await self.request(
            "/v2/miotspec/prop/get", {"datasource": 1, "params": params}
        )
        return list(result.get("result") or [])

    async def set_props(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = await self.request("/v2/miotspec/prop/set", {"params": params})
        return list(result.get("result") or [])

    # ---- MIoT-Spec -----------------------------------------------------
    async def get_spec(self, urn: str) -> dict[str, Any]:
        if urn in self._specs:
            return self._specs[urn]
        if self._spec_cache is not None:
            path = self._spec_cache / f"{hashlib.sha1(urn.encode()).hexdigest()}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text("utf-8"))
                    self._specs[urn] = data
                    return data
                except (OSError, json.JSONDecodeError):
                    pass
        try:
            response = await self._http.get(SPEC_URL, params={"type": urn})
        except httpx.HTTPError as exc:
            raise MijiaCloudError(f"MIoT-Spec 获取失败: {exc}") from exc
        if response.status_code != 200:
            raise MijiaCloudError(
                f"MIoT-Spec 获取失败: HTTP {response.status_code} ({urn})"
            )
        data = response.json()
        if not isinstance(data, dict) or not data.get("services"):
            raise MijiaCloudError(f"MIoT-Spec 内容为空: {urn}")
        self._specs[urn] = data
        if self._spec_cache is not None:
            self._spec_cache.mkdir(parents=True, exist_ok=True)
            path = self._spec_cache / f"{hashlib.sha1(urn.encode()).hexdigest()}.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    async def close(self) -> None:
        await self._http.aclose()


class XiaomiCloudClient(BaseCloudClient):
    """Legacy password login. Kept as a fallback for older accounts."""

    def __init__(
        self,
        username: str,
        password: str,
        country: str = "cn",
        state_dir: Path | None = None,
    ) -> None:
        super().__init__(country, state_dir)
        self.username = username
        self.password = password
        self._mc: Any = None
        self._lock = asyncio.Lock()

    # ---- login / transport --------------------------------------------
    def _login_sync(self) -> None:
        try:
            from micloud import MiCloud
            from micloud.micloudexception import MiCloudAccessDenied
        except ImportError as exc:  # pragma: no cover
            raise MijiaCloudAuthError("未安装 micloud") from exc
        cloud = MiCloud(self.username, self.password)
        try:
            ok = cloud.login()
        except MiCloudAccessDenied as exc:
            raise MijiaCloudAuthError(
                "小米账号拒绝密码登录（403）。小米已限制旧版密码登录；"
                "请改用 OAuth：先运行 `homeone mijia-oauth-login`。"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - micloud raises several types
            raise MijiaCloudAuthError(f"小米云登录异常: {exc}") from exc
        if not ok:
            raise MijiaCloudAuthError(
                "小米账号拒绝密码登录（403）。请改用 OAuth："
                "`homeone mijia-oauth-login`。"
            )
        self._mc = cloud

    async def login(self) -> None:
        async with self._lock:
            if self._mc is None:
                await asyncio.to_thread(self._login_sync)

    async def _ensure_login(self) -> None:
        if self._mc is None:
            await self.login()

    def _request_sync(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        if self._mc is None:
            raise MijiaCloudAuthError("尚未登录小米云")
        payload = {"data": json.dumps(data, separators=(",", ":"), ensure_ascii=False)}
        try:
            response = self._mc.request(self.api_base + path, payload)
        except Exception as exc:  # noqa: BLE001
            raise MijiaCloudError(f"小米云请求失败 ({path}): {exc}") from exc
        if response is None:
            raise MijiaCloudError(f"小米云返回空响应 ({path})")
        if isinstance(response, (bytes, bytearray)):
            response = response.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(response) if isinstance(response, str) else response
        except json.JSONDecodeError as exc:
            raise MijiaCloudError(f"小米云响应不是 JSON ({path}): {response[:120]}") from exc
        if not isinstance(parsed, dict):
            raise MijiaCloudError(f"小米云响应格式异常 ({path})")
        code = parsed.get("code")
        if code not in (None, 0):
            raise MijiaCloudError(
                f"小米云错误 ({path}): code={code} message={parsed.get('message')}"
            )
        return parsed

    async def request(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_login()
        try:
            async with self._lock:
                return await asyncio.to_thread(self._request_sync, path, data)
        except MijiaCloudError:
            # micloud clears service_token on a 403; re-login once before
            # giving up so a long-running gateway survives token expiry.
            if self._mc is not None and not getattr(self._mc, "service_token", None):
                async with self._lock:
                    self._mc = None
                    await asyncio.to_thread(self._login_sync)
                async with self._lock:
                    return await asyncio.to_thread(self._request_sync, path, data)
            raise

    async def _fallback_dids(self) -> list[str]:
        if self._mc is None:
            return []
        try:
            devices = await asyncio.to_thread(self._mc.get_devices, self.country)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("小米云 v1 设备列表失败: %s", exc)
            return []
        return _unique(
            [str(item.get("did")) for item in devices or [] if item.get("did")]
        )

    async def close(self) -> None:
        await super().close()
        if self._mc is not None:
            session = getattr(self._mc, "session", None)
            if session is not None:
                await asyncio.to_thread(session.close)
            self._mc = None


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]
