"""Xiaomi OAuth 2.0 transport for the Mijia cloud provider.

Xiaomi now rejects the legacy password login used by ``micloud``. This module
implements the browser authorization-code flow used by the official Xiaomi
Home integration. The public OAuth client id is the one shipped in
``XiaoMi/ha_xiaomi_home``; the authorization page is handled by Xiaomi and
supports two-factor verification.

The default redirect host is ``homeassistant.local:8123`` because that is the
registered redirect for that client. The browser will normally fail to open
it (no Home Assistant on the laptop); copy the full address bar URL back into
the CLI instead — the URL still contains the authorization code.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from homekit_one.providers.mijia_cloud.client import (
    BaseCloudClient,
    MijiaCloudAuthError,
    MijiaCloudError,
)

OAUTH_CLIENT_ID = "2882303761520251711"
OAUTH_AUTH_URL = "https://account.xiaomi.com/oauth2/authorize"
DEFAULT_REDIRECT_URI = "http://homeassistant.local:8123"
TOKEN_PATH = "/app/v2/ha/oauth/get_token"


def generate_device_id() -> str:
    return f"ha.{uuid.uuid4().hex}"


def state_for(device_id: str) -> str:
    return hashlib.sha1(f"d={device_id}".encode()).hexdigest()


def build_auth_url(
    device_id: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    client_id: str = OAUTH_CLIENT_ID,
) -> str:
    params = {
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "response_type": "code",
        "device_id": device_id,
        "state": state_for(device_id),
        "skip_confirm": "true",
    }
    return f"{OAUTH_AUTH_URL}?{urlencode(params)}"


def parse_callback(value: str) -> tuple[str, str | None]:
    """Accept either a full callback URL or a bare authorization code."""
    value = value.strip()
    if not value:
        raise ValueError("回调地址为空")
    code: str | None = None
    state: str | None = None
    if "code=" in value:
        query = urlparse(value).query
        params = parse_qs(query)
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]
    else:
        code = value
    if not code:
        raise ValueError("回调地址里没有 code")
    return code, state


class XiaomiOAuthClient(BaseCloudClient):
    def __init__(
        self,
        country: str,
        state_dir: Path | None = None,
        tokens: dict[str, Any] | None = None,
        device_id: str | None = None,
        redirect_uri: str | None = None,
        client_id: str = OAUTH_CLIENT_ID,
        on_tokens: Any = None,
    ) -> None:
        super().__init__(country, state_dir)
        self._on_tokens = on_tokens
        tokens = tokens or {}
        self.access_token = str(tokens.get("access_token") or "")
        self.refresh_token = str(tokens.get("refresh_token") or "")
        self.expires_at = float(tokens.get("expires_at") or 0)
        self.device_id = (
            device_id
            or tokens.get("device_id")
            or generate_device_id()
        )
        self.redirect_uri = (
            redirect_uri
            or tokens.get("redirect_uri")
            or DEFAULT_REDIRECT_URI
        )
        self.client_id = str(tokens.get("client_id") or client_id)

    @property
    def oauth_host(self) -> str:
        if self.country == "cn":
            return "ha.api.io.mi.com"
        return f"{self.country}.ha.api.io.mi.com"

    @property
    def oauth_base(self) -> str:
        return f"https://{self.oauth_host}"

    def tokens(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "device_id": self.device_id,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
        }

    def auth_url(self) -> str:
        return build_auth_url(self.device_id, self.redirect_uri, self.client_id)

    # ---- token lifecycle ----------------------------------------------
    async def login(self) -> None:
        await self.ensure_token()

    async def ensure_token(self) -> None:
        if self.access_token and self.expires_at > time.time() + 60:
            return
        if self.refresh_token:
            await self.refresh()
            return
        raise MijiaCloudAuthError(
            "尚未完成小米 OAuth 授权，请先运行 `homekit-one mijia-oauth-login`"
        )

    async def exchange_code(self, code: str) -> dict[str, Any]:
        result = await self._token_request(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "code": code,
                "device_id": self.device_id,
            }
        )
        return result

    async def refresh(self) -> dict[str, Any]:
        if not self.refresh_token:
            raise MijiaCloudAuthError("OAuth refresh_token 不存在，请重新授权")
        return await self._token_request(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "refresh_token": self.refresh_token,
            }
        )

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.get(
                f"{self.oauth_base}{TOKEN_PATH}",
                params={"data": json.dumps(data, separators=(",", ":"))},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": f"homekit-one/0.1 client/{self.device_id}",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise MijiaCloudError(f"OAuth token 请求失败: {exc}") from exc
        if response.status_code == 401:
            raise MijiaCloudAuthError("OAuth 授权已失效，请重新运行 mijia-oauth-login")
        if response.status_code != 200:
            raise MijiaCloudError(
                f"OAuth token 请求 HTTP {response.status_code}: {response.text[:160]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MijiaCloudError(
                f"OAuth token 响应不是 JSON: {response.text[:160]}"
            ) from exc
        if payload.get("code") != 0 or not isinstance(payload.get("result"), dict):
            raise MijiaCloudError(f"OAuth token 返回异常: {payload}")
        result = payload["result"]
        access_token = result.get("access_token")
        if not access_token:
            raise MijiaCloudError(f"OAuth 没有返回 access_token: {payload}")
        self.access_token = str(access_token)
        if result.get("refresh_token"):
            self.refresh_token = str(result["refresh_token"])
        try:
            expires_in = float(result.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        self.expires_at = time.time() + max(0, expires_in - 60)
        if self._on_tokens is not None:
            try:
                self._on_tokens(self.tokens())
            except Exception:  # noqa: BLE001 - token persistence must not break requests
                pass
        return result

    # ---- API transport -------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "X-Client-BizId": "haapi",
            "Content-Type": "application/json",
            "Authorization": f"Bearer{self.access_token}",
            "X-Client-AppId": self.client_id,
            "User-Agent": f"homekit-one/0.1 client/{self.device_id}",
        }

    async def request(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_token()
        try:
            return await self._post(path, data)
        except _TokenExpired:
            await self.refresh()
            try:
                return await self._post(path, data)
            except _TokenExpired as exc:
                raise MijiaCloudAuthError(
                    "OAuth token 刷新后仍被拒绝，请重新运行 mijia-oauth-login"
                ) from exc

    async def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        response = await self._http.post(
            f"{self.oauth_base}/app{path}", json=data, headers=self._headers()
        )
        if response.status_code == 401:
            raise _TokenExpired
        if response.status_code != 200:
            raise MijiaCloudError(
                f"小米 OAuth API HTTP {response.status_code} ({path}): "
                f"{response.text[:160]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MijiaCloudError(
                f"小米 OAuth API 响应不是 JSON ({path}): {response.text[:160]}"
            ) from exc
        code = payload.get("code")
        if code not in (None, 0):
            raise MijiaCloudError(
                f"小米 OAuth API 错误 ({path}): code={code} "
                f"message={payload.get('message')}"
            )
        return payload


class _TokenExpired(Exception):
    pass
