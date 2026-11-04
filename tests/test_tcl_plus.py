from __future__ import annotations

import json

import httpx
import respx

from homeone.providers.tcl_plus.client import (
    DEVICE_STATUS_API,
    GET_DEVICES_API,
    LOGIN_API,
    TclPlusClient,
)
from homeone.providers.tcl_plus.crypto import encrypt_body, encrypt_url_params


def test_rsa_payloads_are_non_empty():
    url = encrypt_url_params({"username": "13800138000", "password": "x"})
    assert "username=" in url and "password=" in url
    assert len(url) > 200
    body = encrypt_body({"username": "13800138000"})
    assert len(body) > 100


@respx.mock
async def test_password_login_devices_status_command(tmp_path):
    login_route = respx.route(
        method="POST", url__startswith=LOGIN_API
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "accountId": "acct-1",
                "accessToken": "token-1",
                "refreshToken": "refresh-1",
            },
        )
    )
    respx.get(GET_DEVICES_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "200",
                "data": [
                    {
                        "deviceId": "tv-1",
                        "nickName": "客厅电视",
                        "category": "TV",
                        "productKey": "pk-tv",
                        "isOnline": 1,
                        "weChatControl": "1",
                    }
                ],
            },
        )
    )
    respx.post(DEVICE_STATUS_API).mock(
        return_value=httpx.Response(
            200,
            json={"code": "200", "data": {"status": {"power": 1, "volume": 20}}},
        )
    )
    command_route = respx.post(
        "https://io.zx.tcljd.com/v1/control/property/tv-1"
    ).mock(
        return_value=httpx.Response(200, json={"code": "200", "traceId": "t"})
    )

    client = TclPlusClient(state_dir=tmp_path)
    await client.login_by_password("13800138000", "secret")
    assert client.account_id == "acct-1"
    assert client.access_token == "token-1"
    assert login_route.called

    devices = await client.get_devices()
    assert devices[0]["deviceId"] == "tv-1"
    assert devices[0]["weChatControl"] == "1"

    status = await client.get_snapshot("tv-1")
    assert status == {"power": 1, "volume": 20}

    await client.send_command("tv-1", {"power": 0})
    body = json.loads(command_route.calls[0].request.content)
    assert body["source"] == "miniprogram"
    assert body["params"] == [{"power": 0}]
    await client.close()


@respx.mock
async def test_sms_login(tmp_path):
    respx.get(
        "https://cn.account.tcl.com/captcha/captcha/new/smsCaptcha"
    ).mock(return_value=httpx.Response(200, json={"status": "SUCCESS"}))
    respx.route(method="POST", url__startswith="https://cn.account.tcl.com/auth/auth/quickLogin").mock(
        return_value=httpx.Response(
            200,
            json={
                "accountId": "acct-2",
                "accessToken": "token-2",
                "refreshToken": "refresh-2",
            },
        )
    )
    client = TclPlusClient(state_dir=tmp_path)
    await client.send_sms_captcha("13800138000")
    await client.login_by_sms("13800138000", "123456")
    assert client.access_token == "token-2"
    await client.close()
