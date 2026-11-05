from __future__ import annotations

from homekit_one.providers.mijia_cloud.client import XiaomiCloudClient


def test_api_base_regions(tmp_path):
    assert XiaomiCloudClient("u", "p", "cn", tmp_path).api_base == (
        "https://api.io.mi.com/app"
    )
    assert XiaomiCloudClient("u", "p", "de", tmp_path).api_base == (
        "https://de.api.io.mi.com/app"
    )


async def test_device_records_merge_home_and_rooms(tmp_path, monkeypatch):
    client = XiaomiCloudClient("u", "p", "cn", tmp_path)
    calls: list[str] = []

    async def fake_request(path, data):
        calls.append(path)
        if path == "/v2/homeroom/gethome":
            return {
                "result": {
                    "homelist": [
                        {"dids": ["a"], "roomlist": [{"dids": ["b", "a"]}]}
                    ]
                }
            }
        if path == "/v2/home/device_list_page":
            return {
                "result": {
                    "list": [
                        {"did": did, "name": did, "spec_type": f"urn:{did}"}
                        for did in data["dids"]
                    ]
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(client, "request", fake_request)
    records = await client.get_device_records()
    assert [record["did"] for record in records] == ["a", "b"]
    assert calls == ["/v2/homeroom/gethome", "/v2/home/device_list_page"]
    await client._http.aclose()


async def test_props_roundtrip(tmp_path, monkeypatch):
    client = XiaomiCloudClient("u", "p", "cn", tmp_path)
    seen: list[tuple[str, dict]] = []

    async def fake_request(path, data):
        seen.append((path, data))
        if path == "/v2/miotspec/prop/get":
            return {"result": [{"did": "a", "siid": 2, "piid": 1, "value": True}]}
        if path == "/v2/miotspec/prop/set":
            return {"result": [{"code": 0}]}
        raise AssertionError(path)

    monkeypatch.setattr(client, "request", fake_request)
    got = await client.get_props([{"did": "a", "siid": 2, "piid": 1}])
    assert got[0]["value"] is True
    written = await client.set_props(
        [{"did": "a", "siid": 2, "piid": 1, "value": False}]
    )
    assert written == [{"code": 0}]
    assert seen[0][1]["datasource"] == 1
    await client._http.aclose()


class FakeMiCloud:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.service_token = "token"
        self.ssecurity = "secret"

    def login(self):
        return True

    def request(self, url, params):
        import json

        return json.dumps({"result": {"url": url, "data": params["data"]}}).encode()


async def test_login_and_signed_request_plumbing(tmp_path, monkeypatch):
    monkeypatch.setattr("micloud.MiCloud", FakeMiCloud)
    client = XiaomiCloudClient("u", "p", "cn", tmp_path)
    await client.login()
    result = await client.request("/v2/probe", {"hello": "world"})
    assert result["result"]["url"] == "https://api.io.mi.com/app/v2/probe"
    assert '"hello":"world"' in result["result"]["data"]
    await client._http.aclose()
