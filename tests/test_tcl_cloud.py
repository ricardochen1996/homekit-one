from __future__ import annotations

import time

import httpx
import jwt
import respx

from homeone.providers.tcl.cloud import (
    APP_ID,
    CLOUD_URLS,
    LOGIN_URL,
    TclCloud,
    _jwt_expired,
)

CLOUD = "https://cloud.example"
DEVICE = "https://device.example"


def _token(sub: str = "identity", exp: float | None = None) -> str:
    payload = {"sub": sub, "exp": exp or time.time() + 3600}
    return jwt.encode(payload, "test-key-that-is-long-enough-1234", algorithm="HS256")


@respx.mock
async def test_login_flow(tmp_path):
    auth = _token("user")
    saas = _token("saas")
    cognito = _token("identity")
    respx.post(LOGIN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 1,
                "token": auth,
                "refresh_token": _token("refresh"),
                "user": {"username": "u", "country_abbr": "CN"},
            },
        )
    )
    respx.post(CLOUD_URLS).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "cloud_region": "us-east-1",
                    "cloud_url": CLOUD,
                    "device_url": DEVICE,
                }
            },
        )
    )
    respx.post(f"{CLOUD}/v3/auth/refresh_tokens").mock(
        return_value=httpx.Response(
            200, json={"data": {"saas_token": saas, "cognito_token": cognito}}
        )
    )
    respx.post("https://cognito-identity.us-east-1.amazonaws.com/").mock(
        return_value=httpx.Response(
            200,
            json={
                "IdentityId": "identity",
                "Credentials": {
                    "AccessKeyId": "AK",
                    "SecretKey": "SK",
                    "SessionToken": "ST",
                    "Expiration": time.time() + 3600,
                },
            },
        )
    )

    cloud = TclCloud("u", "p", tmp_path)
    await cloud._login()
    assert cloud.session.username == "u"
    assert cloud.session.cloud_region == "us-east-1"
    assert cloud.session.saas_token == saas
    assert cloud.session.aws_access_key == "AK"
    assert (tmp_path / "tcl_session.json").exists()
    await cloud.close()


def test_jwt_expiry():
    assert _jwt_expired("") is True
    assert _jwt_expired(_token(exp=time.time() - 10)) is True
    assert _jwt_expired(_token(exp=time.time() + 3600)) is False
    assert _jwt_expired("not-a-jwt") is True
    iso = jwt.encode(
        {"sub": "x", "expiredDate": "2099-01-01T00:00:00Z"}, "test-key-that-is-long-enough-1234", algorithm="HS256"
    )
    assert _jwt_expired(iso, "expiredDate") is False


def test_app_id_matches_reference():
    assert APP_ID == "wx6e1af3fa84fbe523"
