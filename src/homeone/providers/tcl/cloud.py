"""TCL Home cloud client.

The flow is the same one used by the TCL Home Android app and by
nemesa/ha-tcl-home-unofficial-integration:
  account login -> cloud URLs -> SaaS/cognito tokens -> Cognito credentials
  -> AWS IoT Data Plane shadow reads and writes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import random
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jwt

_LOGGER = logging.getLogger(__name__)

LOGIN_URL = "https://pa.account.tcl.com/account/login?clientId=54148614"
CLOUD_URLS = "https://prod-center.aws.tcljd.com/v3/global/cloud_url_get"
APP_ID = "wx6e1af3fa84fbe523"

LOGIN_HEADERS = {
    "th_platform": "android",
    "th_version": "4.8.1",
    "th_appbulid": "830",
    "user-agent": "Android",
    "content-type": "application/json; charset=UTF-8",
}


class TclError(RuntimeError):
    pass


class TclAuthError(TclError):
    pass


@dataclass
class TclSession:
    auth_token: str = ""
    refresh_token: str = ""
    username: str = ""
    country_abbr: str = ""
    cloud_region: str = ""
    cloud_url: str = ""
    device_url: str = ""
    saas_token: str = ""
    cognito_token: str = ""
    cognito_id: str = ""
    aws_access_key: str = ""
    aws_secret_key: str = ""
    aws_session_token: str = ""
    aws_expiration: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TclSession:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in allowed})


def _jwt_expired(token: str, claim: str = "exp", skew: int = 60) -> bool:
    if not token:
        return True
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception:  # noqa: BLE001
        return True
    value = payload.get(claim)
    if value is None and claim != "exp":
        value = payload.get("exp")
    try:
        expires = float(value)
    except (TypeError, ValueError):
        if isinstance(value, str):
            try:
                expires = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return True
        else:
            return True
    return expires - skew <= time.time()


def _jwt_sub(token: str) -> str:
    try:
        return str(jwt.decode(token, options={"verify_signature": False}).get("sub", ""))
    except Exception:  # noqa: BLE001
        return ""


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _sign(timestamp: str, nonce: str, token: str) -> str:
    return _md5(timestamp + nonce + token)


def _signed_headers(token: str) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    nonce = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))
    return {
        "platform": "android",
        "appversion": "5.4.1",
        "thomeversion": "4.8.1",
        "accesstoken": token,
        "accept-language": "en",
        "timestamp": timestamp,
        "nonce": nonce,
        "sign": _sign(timestamp, nonce, token),
        "user-agent": "Android",
        "content-type": "application/json; charset=UTF-8",
        "accept-encoding": "gzip, deflate, br",
    }


class TclCloud:
    def __init__(self, username: str, password: str, state_dir: Path) -> None:
        self.username = username
        self.password = password
        self.session_path = state_dir / "tcl_session.json"
        self.session = self._load()
        self._http = httpx.AsyncClient(timeout=20, follow_redirects=True)
        self._iot_client: Any = None
        self._lock = asyncio.Lock()

    # ---- persistence ---------------------------------------------------
    def _load(self) -> TclSession:
        if self.session_path.exists():
            try:
                return TclSession.from_dict(
                    json.loads(self.session_path.read_text("utf-8"))
                )
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        return TclSession()

    def _save(self) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.session_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.chmod(0o600)
        tmp.replace(self.session_path)

    # ---- auth ----------------------------------------------------------
    async def _login(self) -> None:
        self.session = TclSession()
        payload = {
            "equipment": 2,
            "password": _md5(self.password),
            "osType": 1,
            "username": self.username,
            "clientVersion": "4.8.1",
            "osVersion": "6.0",
            "deviceModel": "AndroidAndroid SDK built for x86",
            "captchaRule": 2,
            "channel": "app",
        }
        response = await self._http.post(LOGIN_URL, json=payload, headers=LOGIN_HEADERS)
        if response.status_code != 200:
            raise TclAuthError(f"TCL 登录 HTTP {response.status_code}")
        data = response.json()
        if data.get("status") != 1:
            raise TclAuthError(f"TCL 登录失败: {data.get('message') or data}")
        self.session.auth_token = data.get("token") or ""
        self.session.refresh_token = data.get("refresh_token") or ""
        user = data.get("user") or {}
        self.session.username = user.get("username") or self.username
        self.session.country_abbr = user.get("country_abbr") or user.get("countryAbbr") or ""
        if not self.session.auth_token:
            raise TclAuthError("TCL 登录成功但没有返回 token")
        await self._get_cloud_urls()
        await self._refresh_tokens()
        await self._get_aws_credentials()
        self._save()

    async def _get_cloud_urls(self) -> None:
        response = await self._http.post(
            CLOUD_URLS,
            json={"ssoId": self.session.username, "ssoToken": self.session.auth_token},
            headers={"user-agent": "Android", "content-type": "application/json; charset=UTF-8"},
        )
        data = response.json()
        payload = data.get("data") or {}
        self.session.cloud_region = payload.get("cloud_region") or ""
        self.session.cloud_url = payload.get("cloud_url") or ""
        self.session.device_url = payload.get("device_url") or ""
        if not self.session.cloud_url or not self.session.device_url:
            raise TclAuthError(f"TCL 云地址获取失败: {data}")

    async def _refresh_tokens(self) -> None:
        response = await self._http.post(
            f"{self.session.cloud_url}/v3/auth/refresh_tokens",
            json={
                "userId": self.session.username,
                "ssoToken": self.session.auth_token,
                "appId": APP_ID,
            },
            headers={
                "user-agent": "Android",
                "content-type": "application/json; charset=UTF-8",
                "accept-encoding": "gzip, deflate, br",
            },
        )
        data = response.json()
        payload = data.get("data") or {}
        self.session.saas_token = payload.get("saas_token") or payload.get("saasToken") or ""
        self.session.cognito_token = payload.get("cognito_token") or payload.get("cognitoToken") or ""
        self.session.cognito_id = payload.get("cognito_id") or payload.get("cognitoId") or ""
        if not self.session.saas_token or not self.session.cognito_token:
            raise TclAuthError(f"TCL token 刷新失败: {data}")
        self._iot_client = None

    async def _get_aws_credentials(self) -> None:
        if not self.session.cloud_region:
            raise TclAuthError("缺少 TCL 云区域")
        identity = _jwt_sub(self.session.cognito_token)
        if not identity:
            raise TclAuthError("cognito_token 没有 sub")
        url = f"https://cognito-identity.{self.session.cloud_region}.amazonaws.com/"
        response = await self._http.post(
            url,
            json={
                "IdentityId": identity,
                "Logins": {"cognito-identity.amazonaws.com": self.session.cognito_token},
            },
            headers={
                "User-agent": "aws-sdk-android/2.22.6",
                "X-Amz-Target": "AWSCognitoIdentityService.GetCredentialsForIdentity",
                "content-type": "application/x-amz-json-1.1",
            },
        )
        data = response.json()
        credentials = data.get("Credentials") or {}
        self.session.aws_access_key = credentials.get("AccessKeyId") or ""
        self.session.aws_secret_key = credentials.get("SecretKey") or ""
        self.session.aws_session_token = credentials.get("SessionToken") or ""
        expiration = credentials.get("Expiration")
        if isinstance(expiration, (int, float)):
            self.session.aws_expiration = float(expiration)
        elif isinstance(expiration, str):
            try:
                self.session.aws_expiration = float(expiration)
            except ValueError:
                try:
                    self.session.aws_expiration = dt.datetime.fromisoformat(
                        expiration.replace("Z", "+00:00")
                    ).timestamp()
                except ValueError:
                    self.session.aws_expiration = 0.0
        if not self.session.aws_access_key:
            raise TclAuthError(f"AWS 凭据获取失败: {data}")
        self._iot_client = None

    async def ensure_session(self) -> TclSession:
        async with self._lock:
            if _jwt_expired(self.session.auth_token) or _jwt_expired(
                self.session.refresh_token
            ):
                await self._login()
            elif not self.session.cloud_url or not self.session.device_url:
                await self._login()
            elif _jwt_expired(self.session.saas_token, "expiredDate") or _jwt_expired(
                self.session.cognito_token
            ):
                await self._refresh_tokens()
                await self._get_aws_credentials()
                self._save()
            elif not self.session.aws_access_key or self.session.aws_expiration - 60 <= time.time():
                await self._get_aws_credentials()
                self._save()
            return self.session

    # ---- cloud calls ---------------------------------------------------
    async def get_things(self) -> list[dict[str, Any]]:
        session = await self.ensure_session()
        response = await self._http.post(
            f"{session.device_url}/v3/user/get_things",
            json={},
            headers=_signed_headers(session.saas_token),
        )
        if response.status_code != 200:
            raise TclError(f"get_things HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        if data.get("code") not in (0, 200, "0", "200", None):
            raise TclError(f"get_things 失败: {data.get('message') or data}")
        return list(data.get("data") or [])

    def _iot(self) -> Any:
        if self._iot_client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise TclError("未安装 boto3") from exc
            session = boto3.session.Session(
                region_name=self.session.cloud_region,
                aws_access_key_id=self.session.aws_access_key,
                aws_secret_access_key=self.session.aws_secret_key,
                aws_session_token=self.session.aws_session_token,
            )
            self._iot_client = session.client("iot-data")
        return self._iot_client

    def _get_shadow_sync(self, device_id: str) -> dict[str, Any]:
        response = self._iot().get_thing_shadow(thingName=device_id)
        payload = response["payload"].read().decode("utf-8")
        return json.loads(payload)

    async def get_shadow(self, device_id: str) -> dict[str, Any]:
        await self.ensure_session()
        return await asyncio.to_thread(self._get_shadow_sync, device_id)

    def _set_desired_sync(self, device_id: str, desired: dict[str, Any]) -> None:
        payload = json.dumps(
            {
                "state": {"desired": desired},
                "clientToken": f"homeone_{int(time.time() * 1000)}",
            }
        )
        self._iot().publish(
            topic=f"$aws/things/{device_id}/shadow/update", qos=1, payload=payload
        )

    async def set_desired(self, device_id: str, desired: dict[str, Any]) -> None:
        await self.ensure_session()
        await asyncio.to_thread(self._set_desired_sync, device_id, desired)

    async def close(self) -> None:
        await self._http.aclose()
        self._iot_client = None
