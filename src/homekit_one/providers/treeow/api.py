"""Treeow (树新风) cloud API client."""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

BASE_EZIOTES = "https://eziotes.treeow.com.cn"
BASE_EZOPEN = "https://ezopen.treeow.com.cn"
LOGIN = "/api/user/account/login"
HOME_LIST = "/api/resource/home/list"
DEVICE_LIST = "/api/resource/v3/device/list/page"
DEVICE_INFO = "/api/resource/device/info"
DEVICE_TOKEN = "/api" + "/v3/userdevices" + "/v1/token"
DEVICE_CONTROL = "/api/resource/v3/device/otap/prop"

CLIENT_TYPE = "3"
JSON_HEADERS = {
    "clienttype": CLIENT_TYPE,
    "content-type": "application/json; charset=utf-8",
    "accept-encoding": "gzip",
    "user-agent": "okhttp/3.14.9",
}


class TreeowError(RuntimeError):
    pass


class TreeowAuthError(TreeowError):
    pass


class TreeowAPI:
    def __init__(self, account: str, password: str) -> None:
        self.account = account
        self.password = password
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.open_access_token: str | None = None
        self.secret_key: str | None = None
        self.user_id: int | None = None
        self.device_token: str | None = None
        self._http = httpx.AsyncClient(timeout=20, follow_redirects=True)
        self._logged_in = False

    async def login(self) -> None:
        terminal = uuid.uuid4().hex
        payload = {
            "account": self.account,
            "password": hashlib.md5(self.password.encode("utf-8")).hexdigest(),
            "terminalIdentifier": terminal,
            "terminalName": "".join(str(ord(c)) for c in terminal[:5]),
        }
        response = await self._http.post(
            f"{BASE_EZIOTES}{LOGIN}", json=payload, headers=JSON_HEADERS
        )
        if response.status_code != 200:
            raise TreeowAuthError(f"树新风登录 HTTP {response.status_code}")
        data = response.json()
        if (data.get("meta") or {}).get("code") != 200:
            raise TreeowAuthError(
                f"树新风登录失败: {(data.get('meta') or {}).get('message') or data}"
            )
        login_data = data.get("data") or {}
        self.access_token = login_data.get("accessToken")
        self.refresh_token = login_data.get("refreshToken")
        self.open_access_token = login_data.get("openAccessToken")
        self.secret_key = login_data.get("secretKey")
        self.user_id = login_data.get("userId")
        if not self.access_token:
            raise TreeowAuthError("树新风登录成功但没有 accessToken")
        self._logged_in = True
        await self.get_device_token()

    def _auth_headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.access_token}",
            "clienttype": CLIENT_TYPE,
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "user-agent": "okhttp/3.14.9",
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        response = await self._http.request(
            method, url, json=json, headers=headers or self._auth_headers()
        )
        if response.status_code in (401, 403) and retry_auth:
            _LOGGER.info("树新风 token 失效，重新登录")
            await self.login()
            retry_headers = dict(headers or self._auth_headers())
            retry_headers["authorization"] = f"Bearer {self.access_token}"
            return await self._request(
                method, url, json=json, headers=retry_headers, retry_auth=False
            )
        if response.status_code != 200:
            raise TreeowError(f"树新风 HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

    async def get_device_token(self) -> None:
        if not self.access_token or not self.open_access_token:
            raise TreeowAuthError("尚未登录")
        headers = {
            "authorization": f"Bearer {self.access_token}",
            "clienttype": CLIENT_TYPE,
            "accesstoken": self.open_access_token,
            "clientversion": "30",
            "content-type": "application/json",
            "accept-encoding": "gzip",
            "user-agent": "okhttp/3.14.9",
        }
        data = await self._request(
            "GET", f"{BASE_EZOPEN}{DEVICE_TOKEN}", headers=headers
        )
        if (data.get("meta") or {}).get("code") != 200:
            raise TreeowAuthError(
                f"树新风设备 token 获取失败: {(data.get('meta') or {}).get('message') or data}"
            )
        self.device_token = (data.get("deviceTokenInfo") or {}).get("token")

    async def get_homes(self) -> list[dict[str, Any]]:
        data = await self._request("POST", f"{BASE_EZIOTES}{HOME_LIST}", json={})
        if str(data.get("code")) != "200":
            raise TreeowError(f"树新风家庭列表失败: {data.get('msg') or data}")
        result = data.get("data")
        return result if isinstance(result, list) else []

    async def get_device_list(self, group_id: str) -> list[dict[str, Any]]:
        data = await self._request(
            "POST",
            f"{BASE_EZIOTES}{DEVICE_LIST}",
            json={"pageNo": 1, "pageSize": 100, "groupId": group_id},
        )
        if str(data.get("code")) != "200":
            raise TreeowError(f"树新风设备列表失败: {data.get('msg') or data}")
        result = data.get("data")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("records", "list", "items"):
                if isinstance(result.get(key), list):
                    return result[key]
        return []

    async def get_device_info(self, serial: str) -> dict[str, Any]:
        data = await self._request(
            "POST", f"{BASE_EZIOTES}{DEVICE_INFO}", json={"deviceSerial": serial}
        )
        if str(data.get("code")) != "200":
            raise TreeowError(f"树新风设备信息失败: {data.get('msg') or data}")
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    async def control(
        self,
        serial: str,
        prop: str,
        value: Any,
        domain: str = "Air_Purifier",
        resource_category: str = "default",
        local_index: str = "0",
    ) -> None:
        headers = {
            "authorization": f"Bearer {self.access_token}",
            "deviceserial": serial,
            "clienttype": CLIENT_TYPE,
            "domainidentifier": domain,
            "localindex": local_index,
            "propidentifier": prop,
            "resourcecategory": resource_category,
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "user-agent": "okhttp/3.14.9",
        }
        data = await self._request(
            "PUT", f"{BASE_EZIOTES}{DEVICE_CONTROL}", json={"value": value}, headers=headers
        )
        if (data.get("meta") or {}).get("code") != 200:
            raise TreeowError(
                f"树新风控制失败: {(data.get('meta') or {}).get('message') or data}"
            )

    async def close(self) -> None:
        await self._http.aclose()
