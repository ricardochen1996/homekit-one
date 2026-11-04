"""TCL+ (China) cloud client.

Protocol ported from the Apache-2.0 project ndwzy/tcl-ha. It supports the
three login methods the reference supports: phone + password (RSA-encrypted),
SMS code, and a captured account_id + refreshToken pair.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from homeone.providers.tcl_plus import crypto
from homeone.providers.tcl_plus.constants import (
    APP_ENCRYPT_VERSION,
    APP_ID,
    APP_PLATFORM_TYPE,
    APP_SECRET,
    APP_TENANT_ID,
    APP_UUID,
    APP_VERSION,
    IOS_APP_ID,
    IOS_APP_SECRET,
    IOS_APP_VERSION,
    IOS_PLATFORM_TYPE,
    IOS_REPORT_STATE,
    IOS_STORE_UUID,
    IOS_TENANT_ID,
    IOS_USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

REFRESH_TOKEN_API = "https://cn.account.tcl.com/auth/auth/refershToken"
LOGIN_API = "https://cn.account.tcl.com/auth/auth/login"
QUICK_LOGIN_API = "https://cn.account.tcl.com/auth/auth/quickLogin"
SMS_CAPTCHA_API = "https://cn.account.tcl.com/captcha/captcha/new/smsCaptcha"
GET_USER_INFO_API = "https://cn.account.tcl.com/user/user/getUserInfoByToken"
GET_DEVICES_API = "https://io.zx.tcljd.com/v1/tclplus/weChat/user/user_devices"
CONTROL_DEVICE_API = "https://io.zx.tcljd.com/v1/control/property/{device_id}"
DEVICE_STATUS_API = "https://io.zx.tcljd.com/v1/thing/status"
GET_DIGITAL_MODEL_API = "https://io.zx.tcljd.com/v1/tclplus/panel/rn-panel-config"
MINI_PROGRAM_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13)XWEB/13639"
)
MINI_REFERER = "https://servicewechat.com/wxed3f11c6ee178737/243/page-frame.html"


class TclPlusError(RuntimeError):
    pass


class TclPlusAuthError(TclPlusError):
    pass


class TclPlusClient:
    def __init__(
        self,
        phone: str = "",
        password: str = "",
        account_id: str = "",
        refresh_token: str = "",
        state_dir: Path | None = None,
    ) -> None:
        self.phone = phone
        self.password = password
        self.account_id = account_id
        self.refresh_token = refresh_token
        self.access_token = ""
        self.state_dir = state_dir
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._lock = asyncio.Lock()

    # ---- headers -------------------------------------------------------
    @staticmethod
    def _login_headers() -> dict[str, str]:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "t-platform-type": IOS_PLATFORM_TYPE,
            "uid": "",
            "Accept": "*/*",
            "cid": str(uuid.uuid4()).upper(),
            "Accept-Language": "zh-Hans-CN;q=1, zh-Hant-CN;q=0.9, en-CN;q=0.8",
            "token": "",
            "EncryptVersion": "2.0",
            "t-app-version": IOS_APP_VERSION,
            "t-store-uuid": IOS_STORE_UUID,
            "Encrypt": "true",
            "User-Agent": IOS_USER_AGENT,
            "t-application-name": "",
        }

    def _io_headers(
        self, content_type: str = "application/x-www-form-urlencoded;charset=utf-8"
    ) -> dict[str, str]:
        return {
            "Host": "io.zx.tcljd.com",
            "accessToken": self.access_token,
            "t-app-version": APP_VERSION,
            "t-store-uuid": APP_UUID,
            "User-Agent": MINI_PROGRAM_UA,
            "Content-Type": content_type,
            "xweb_xhr": "1",
            "miniGramSetup": "1",
            "t-platform-type": APP_PLATFORM_TYPE,
            "Referer": MINI_REFERER,
        }

    def _account_headers(self, content_type: str) -> dict[str, str]:
        return {
            "Host": "cn.account.tcl.com",
            "TCL-Authorization": self.access_token,
            "token": self.access_token,
            "EncryptVersion": APP_ENCRYPT_VERSION,
            "t-app-version": APP_VERSION,
            "t-store-uuid": APP_UUID,
            "t-platform-type": APP_PLATFORM_TYPE,
            "User-Agent": MINI_PROGRAM_UA,
            "Content-Type": content_type,
            "xweb_xhr": "1",
            "Referer": MINI_REFERER,
        }

    # ---- login ---------------------------------------------------------
    async def login(self) -> dict[str, Any]:
        if self.account_id and self.refresh_token:
            return await self.login_with_refresh_token()
        if self.phone and self.password:
            return await self.login_by_password(self.phone, self.password)
        raise TclPlusAuthError(
            "需要 phone+password、account_id+refreshToken 或短信验证码"
        )

    async def login_by_password(self, phone: str, password: str) -> dict[str, Any]:
        params = {
            "deviceId": str(uuid.uuid4()).upper(),
            "password": crypto.md5_hash(password),
            "channel": IOS_STORE_UUID,
            "tenantId": IOS_TENANT_ID,
            "appSecret": IOS_APP_SECRET,
            "username": phone,
            "appId": IOS_APP_ID,
            "reportState": IOS_REPORT_STATE,
        }
        url = f"{LOGIN_API}?{crypto.encrypt_url_params(params)}"
        body = crypto.encrypt_body(params)
        try:
            response = await self._http.post(
                url, content=body, headers=self._login_headers()
            )
        except httpx.HTTPError as exc:
            raise TclPlusError(f"TCL+ 登录请求失败: {exc}") from exc
        result = _json(response, "登录")
        if not result.get("accessToken"):
            raise TclPlusAuthError(
                f"TCL+ 登录失败: {result.get('message') or result}"
            )
        self._set_tokens(result)
        self.phone = phone
        self.password = password
        return result

    async def send_sms_captcha(self, phone: str) -> None:
        params = {
            "appId": IOS_APP_ID,
            "appSecret": IOS_APP_SECRET,
            "tenantId": IOS_TENANT_ID,
            "timestamp": str(int(time.time() * 1000)),
            "nonce": crypto.generate_nonce(),
            "mobile": phone,
            "bType": "LOGIN",
        }
        params["sign"] = crypto.generate_sign(params)
        url = f"{SMS_CAPTCHA_API}?{crypto.encrypt_url_params(params)}"
        try:
            response = await self._http.get(url, headers=self._login_headers())
        except httpx.HTTPError as exc:
            raise TclPlusError(f"短信验证码请求失败: {exc}") from exc
        result = _json(response, "短信验证码")
        status = str(result.get("status", ""))
        code = result.get("code")
        if status != "SUCCESS" and code not in (1, 200, 0, "1", "200", "0"):
            raise TclPlusAuthError(
                f"短信发送失败: {result.get('message') or result}"
            )

    async def login_by_sms(self, phone: str, code: str) -> dict[str, Any]:
        params = {
            "deviceId": str(uuid.uuid4()).upper(),
            "bType": "LOGIN",
            "appSecret": IOS_APP_SECRET,
            "username": phone,
            "appId": IOS_APP_ID,
            "tenantId": IOS_TENANT_ID,
            "validCode": code,
            "reportState": IOS_REPORT_STATE,
        }
        url = f"{QUICK_LOGIN_API}?{crypto.encrypt_url_params(params)}"
        body = crypto.encrypt_body(params)
        try:
            response = await self._http.post(
                url, content=body, headers=self._login_headers()
            )
        except httpx.HTTPError as exc:
            raise TclPlusError(f"短信登录请求失败: {exc}") from exc
        result = _json(response, "短信登录")
        if not result.get("accessToken"):
            raise TclPlusAuthError(
                f"TCL+ 短信登录失败: {result.get('message') or result}"
            )
        self._set_tokens(result)
        self.phone = phone
        return result

    async def login_with_refresh_token(self) -> dict[str, Any]:
        if not self.account_id or not self.refresh_token:
            raise TclPlusAuthError("缺少 account_id 或 refreshToken")
        token = await self.refresh()
        info = await self.get_user_info()
        return {
            "accountId": self.account_id,
            "accessToken": token,
            "refreshToken": self.refresh_token,
            "mobile": info.get("mobile"),
        }

    async def refresh(self) -> str:
        url = (
            f"{REFRESH_TOKEN_API}?appId={APP_ID}&accountId={self.account_id}"
            f"&tenantId={APP_TENANT_ID}&appSecret={APP_SECRET}"
        )
        headers = {
            "Host": "cn.account.tcl.com",
            "Content-Type": "application/json;charset=UTF-8",
            "t-platform-type": "iOS",
            "TCL-Authorization": self.access_token,
            "token": self.access_token,
            "EncryptVersion": APP_ENCRYPT_VERSION,
            "t-app-version": APP_VERSION,
            "t-store-uuid": APP_UUID,
            "User-Agent": IOS_USER_AGENT,
            "Encrypt": "false",
            "refreshToken": self.refresh_token,
        }
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise TclPlusError(f"TCL+ token 刷新失败: {exc}") from exc
        result = _json(response, "token 刷新")
        if not result.get("accessToken"):
            raise TclPlusAuthError(
                f"TCL+ token 刷新失败: {result.get('message') or result}"
            )
        self.access_token = str(result["accessToken"])
        self.refresh_token = str(result.get("refreshToken") or self.refresh_token)
        return self.access_token

    def _set_tokens(self, result: dict[str, Any]) -> None:
        self.account_id = str(result.get("accountId") or self.account_id)
        self.access_token = str(result.get("accessToken") or "")
        self.refresh_token = str(result.get("refreshToken") or self.refresh_token)

    # ---- account / devices --------------------------------------------
    async def get_user_info(self) -> dict[str, Any]:
        url = (
            f"{GET_USER_INFO_API}?appId={APP_ID}&tenantId={APP_TENANT_ID}"
            f"&appSecret={APP_SECRET}"
        )
        headers = self._account_headers("application/x-www-form-urlencoded;charset=utf-8")
        headers["Referer"] = MINI_REFERER
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise TclPlusError(f"TCL+ 用户信息获取失败: {exc}") from exc
        result = _json(response, "用户信息")
        if "FAILED" in str(result.get("status", "")):
            raise TclPlusAuthError(
                f"TCL+ 用户信息失败: {result.get('failCause') or result}"
            )
        data = result.get("data") or {}
        return {
            "accountId": str(data.get("accountId") or self.account_id),
            "mobile": data.get("phone"),
            "username": data.get("username"),
        }

    async def get_devices(self) -> list[dict[str, Any]]:
        try:
            response = await self._http.get(
                GET_DEVICES_API, headers=self._io_headers()
            )
        except httpx.HTTPError as exc:
            raise TclPlusError(f"TCL+ 设备列表失败: {exc}") from exc
        result = _json(response, "设备列表")
        self._assert_ok(result, "设备列表")
        return list(result.get("data") or [])

    async def get_digital_model(self, product_key: str) -> list[dict[str, Any]]:
        try:
            response = await self._http.get(
                f"{GET_DIGITAL_MODEL_API}?productKey={product_key}",
                headers=self._io_headers(),
            )
        except httpx.HTTPError as exc:
            raise TclPlusError(f"TCL+ 面板配置失败: {exc}") from exc
        result = _json(response, "面板配置")
        self._assert_ok(result, "面板配置")
        data = result.get("data") or {}
        pages = data.get("pages") or {}
        return list(pages.get("home") or [])

    async def get_snapshot(self, device_id: str) -> dict[str, Any]:
        try:
            response = await self._http.post(
                DEVICE_STATUS_API,
                headers=self._io_headers("application/json;charset=UTF-8"),
                json={"deviceId": device_id},
            )
        except httpx.HTTPError as exc:
            raise TclPlusError(f"TCL+ 状态读取失败: {exc}") from exc
        result = _json(response, "状态读取")
        self._assert_ok(result, "状态读取")
        data = result.get("data") or {}
        return dict(data.get("status") or {})

    async def send_command(
        self, device_id: str, attributes: dict[str, Any]
    ) -> dict[str, Any]:
        params = [{str(key): value} for key, value in attributes.items()]
        body = {
            "msgId": f"miniprogram_{int(random.random() * 1e5)}_{int(time.time() * 1000)}",
            "source": "miniprogram",
            "version": "3.0.0",
            "params": params,
        }
        try:
            response = await self._http.post(
                CONTROL_DEVICE_API.format(device_id=device_id),
                headers=self._io_headers("application/json;charset=UTF-8"),
                json=body,
            )
        except httpx.HTTPError as exc:
            raise TclPlusError(f"TCL+ 控制失败: {exc}") from exc
        result = _json(response, "控制")
        self._assert_ok(result, "控制")
        return result

    @staticmethod
    def _assert_ok(result: dict[str, Any], action: str) -> None:
        code = result.get("code")
        if "traceId" in result and str(code) not in ("200", "0"):
            raise TclPlusError(
                f"TCL+ {action}失败: {result.get('message') or result}"
            )

    async def close(self) -> None:
        await self._http.aclose()


def _json(response: httpx.Response, action: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise TclPlusError(
            f"TCL+ {action} HTTP {response.status_code}: {response.text[:160]}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise TclPlusError(
            f"TCL+ {action}响应不是 JSON: {response.text[:160]}"
        ) from exc
    if not isinstance(data, dict):
        raise TclPlusError(f"TCL+ {action}响应格式异常")
    return data
