from __future__ import annotations

import json

import pytest

from homeone.providers.mijia import crypto
from homeone.providers.mijia.discovery import devices_from_replies


def _reply(device_id: int, stamp: int = 1234, token: bytes | None = None) -> bytes:
    header = crypto.MAGIC + (32).to_bytes(2, "big") + b"\x00" * 4
    header += device_id.to_bytes(4, "big") + stamp.to_bytes(4, "big")
    return header + (token or b"\x00" * 16)


def test_packet_roundtrip():
    token = b"\x00" * 16
    payload = json.dumps({"id": 1, "method": "get_properties", "params": []}).encode()
    packet = crypto.encode(payload, token, 0x12345678, 42)
    device_id, stamp, decoded = crypto.decode(packet, token)
    assert device_id == 0x12345678
    assert stamp == 42
    assert json.loads(decoded.rstrip(b"\x00"))["method"] == "get_properties"


def test_wrong_token_is_rejected():
    packet = crypto.encode(b'{"id":1}', b"\x01" * 16, 1, 1)
    with pytest.raises(crypto.MiioProtocolError):
        crypto.decode(packet, b"\x02" * 16)


def test_corrupt_packet_is_rejected():
    with pytest.raises(crypto.MiioProtocolError):
        crypto.decode(b"not a packet", b"\x00" * 16)


def test_discovery_parses_hello_replies():
    devices = devices_from_replies([_reply(0x1234), _reply(0x1234), _reply(0xFFFFFFFF)])
    assert len(devices) == 1
    assert devices[0].device_id == 0x1234
    assert devices[0].did == str(0x1234)
    assert devices[0].token is None


def test_discovery_exposes_old_firmware_token():
    token = bytes.fromhex("ab" * 16)
    devices = devices_from_replies([_reply(0x42, token=token)])
    assert devices[0].token == token.hex()
