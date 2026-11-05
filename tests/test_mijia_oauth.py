from __future__ import annotations

import json
import time

import httpx
import respx

from homekit_one.providers.mijia_cloud.oauth import (
    OAUTH_CLIENT_ID,
    XiaomiOAuthClient,
    build_auth_url,
    generate_device_id,
    parse_callback,
    state_for,
)


def test_auth_url_and_callback_parsing():
    device_id = generate_device_id()
    url = build_auth_url(device_id)
    assert "account.xiaomi.com/oauth2/authorize" in url
    assert OAUTH_CLIENT_ID in url
    assert "homeassistant.local" in url
    assert state_for(device_id) in url

    callback = f"http://homeassistant.local:8123/?code=abc123&state={state_for(device_id)}"
    code, state = parse_callback(callback)
    assert code == "abc123"
    assert state == state_for(device_id)
    assert parse_callback("bare-code") == ("bare-code", None)


@respx.mock
async def test_exchange_and_request_headers(tmp_path):
    token_route = respx.get(
        "https://ha.api.io.mi.com/app/v2/ha/oauth/get_token"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "result": {
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                },
            },
        )
    )
    api_route = respx.post(
        "https://ha.api.io.mi.com/app/v2/home/device_list_page"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "result": {"list": [{"did": "d1", "name": "n"}]}},
        )
    )
    client = XiaomiOAuthClient("cn", tmp_path, device_id="ha.test")
    result = await client.exchange_code("code-1")
    assert result["access_token"] == "access-1"
    assert client.tokens()["refresh_token"] == "refresh-1"
    assert token_route.called

    await client.request("/v2/home/device_list_page", {"dids": []})
    request = api_route.calls[0].request
    assert request.headers["authorization"] == "Beareraccess-1"
    assert request.headers["x-client-appid"] == OAUTH_CLIENT_ID
    assert request.headers["x-client-bizid"] == "haapi"
    await client.close()


@respx.mock
async def test_refresh_on_401(tmp_path):
    respx.get("https://ha.api.io.mi.com/app/v2/ha/oauth/get_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "result": {
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                },
            },
        )
    )
    api = respx.post("https://ha.api.io.mi.com/app/v2/miotspec/prop/get")
    api.side_effect = [
        httpx.Response(401),
        httpx.Response(200, json={"code": 0, "result": []}),
    ]
    client = XiaomiOAuthClient(
        "cn",
        tmp_path,
        tokens={
            "access_token": "old",
            "refresh_token": "refresh-old",
            "expires_at": time.time() + 3600,
            "device_id": "ha.test",
        },
    )
    result = await client.request("/v2/miotspec/prop/get", {"params": []})
    assert result["result"] == []
    assert client.access_token == "access-2"
    assert len(api.calls) == 2
    await client.close()


def test_tokens_are_serializable():
    client = XiaomiOAuthClient("cn", None, device_id="ha.test")
    tokens = client.tokens()
    assert json.loads(json.dumps(tokens))["device_id"] == "ha.test"
