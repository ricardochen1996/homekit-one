"""Async wrapper around the miIO UDP protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from typing import Any

from homekit_one.providers.mijia import crypto
from homekit_one.providers.mijia.crypto import MiioProtocolError

_LOGGER = logging.getLogger(__name__)

CLOCK_MAX_AGE = 600.0
PROPERTIES_PER_REQUEST = 15


class MiioError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"miIO error {code}: {message}")
        self.code = code
        self.message = message


class MiioClient:
    def __init__(
        self,
        host: str,
        token: str,
        port: int = crypto.PORT,
        timeout: float = 4.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.token = crypto.token_bytes(token)
        self.device_id: int | None = None
        self.stamp: int = 0
        self._clock_at: float = 0.0
        self._request_id = 0
        self._lock = asyncio.Lock()

    # ---- transport -----------------------------------------------------
    def _exchange_sync(self, packet: bytes, timeout: float) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (self.host, self.port))
            data, _addr = sock.recvfrom(4096)
            return data
        finally:
            sock.close()

    async def _exchange(self, packet: bytes, timeout: float | None = None) -> bytes:
        return await asyncio.to_thread(
            self._exchange_sync, packet, timeout if timeout is not None else self.timeout
        )

    # ---- handshake -----------------------------------------------------
    async def handshake(self) -> int:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                reply = await self._exchange(crypto.HELLO, timeout=1.5)
                device_id, stamp, _checksum = crypto.parse_header(reply)
                if device_id in (0, 0xFFFFFFFF):
                    raise MiioProtocolError("设备返回了无效 ID")
                self.device_id = device_id
                self.stamp = stamp
                self._clock_at = time.monotonic()
                return device_id
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                await asyncio.sleep(0.25 * (attempt + 1))
        raise MiioProtocolError(f"{self.host} 没有响应 miIO 握手: {last_error}")

    def _ensure_clock(self) -> None:
        if self.device_id is None or time.monotonic() - self._clock_at > CLOCK_MAX_AGE:
            raise MiioProtocolError("需要重新握手")

    # ---- request -------------------------------------------------------
    async def request(self, method: str, params: Any, timeout: float | None = None) -> Any:
        async with self._lock:
            try:
                self._ensure_clock()
            except MiioProtocolError:
                await self.handshake()

            last_error: Exception | None = None
            for _ in range(2):
                try:
                    return await self._request_once(method, params, timeout)
                except (TimeoutError, MiioProtocolError) as exc:
                    last_error = exc
                    # A stale clock or a changed DHCP address needs a fresh hello.
                    await self.handshake()
                    await asyncio.sleep(0.15)
            raise MiioProtocolError(f"{method} 失败: {last_error}")

    async def _request_once(self, method: str, params: Any, timeout: float | None) -> Any:
        assert self.device_id is not None
        self._request_id = 1 if self._request_id >= 9999 else self._request_id + 1
        body = json.dumps(
            {"id": self._request_id, "method": method, "params": params},
            separators=(",", ":"),
        ).encode("utf-8") + b"\x00"
        elapsed = int(time.monotonic() - self._clock_at)
        stamp = (self.stamp + elapsed + 1) & 0xFFFFFFFF
        packet = crypto.encode(body, self.token, self.device_id, stamp)
        reply = await self._exchange(packet, timeout)
        _device_id, reply_stamp, payload = crypto.decode(reply, self.token)
        self.stamp = reply_stamp
        self._clock_at = time.monotonic()
        parsed = crypto.parse_payload(payload)
        if "error" in parsed:
            error = parsed["error"]
            raise MiioError(int(error.get("code", -1)), str(error.get("message", "")))
        return parsed.get("result")

    # ---- MIoT ----------------------------------------------------------
    async def get_properties(self, props: list[tuple[int, int]]) -> dict[tuple[int, int], Any]:
        if self.device_id is None:
            await self.handshake()
        did = str(self.device_id)
        values: dict[tuple[int, int], Any] = {}
        for start in range(0, len(props), PROPERTIES_PER_REQUEST):
            chunk = props[start : start + PROPERTIES_PER_REQUEST]
            params = [{"did": did, "siid": siid, "piid": piid} for siid, piid in chunk]
            result = await self.request("get_properties", params)
            for item in result or []:
                try:
                    siid, piid = int(item["siid"]), int(item["piid"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (siid, piid) in chunk and int(item.get("code", 0)) == 0 and "value" in item:
                    values[(siid, piid)] = item["value"]
        return values

    async def set_properties(self, writes: list[tuple[int, int, Any]]) -> None:
        if not writes:
            return
        if self.device_id is None:
            await self.handshake()
        did = str(self.device_id)
        params = [
            {"did": did, "siid": siid, "piid": piid, "value": value}
            for siid, piid, value in writes
        ]
        result = await self.request("set_properties", params)
        for item in result or []:
            if int(item.get("code", 0)) != 0:
                raise MiioError(int(item.get("code", -1)), f"属性写入失败: {item}")

    async def info(self) -> dict[str, Any]:
        result = await self.request("miIO.info", [])
        return result if isinstance(result, dict) else {}
