"""miIO broadcast discovery (no token required)."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass

from homekit_one.providers.mijia import crypto


@dataclass
class DiscoveredDevice:
    host: str
    device_id: int
    token: str | None = None

    @property
    def did(self) -> str:
        return str(self.device_id)

    @property
    def hex_id(self) -> str:
        return f"0x{self.device_id:08x}"


def devices_from_replies(replies: list[bytes]) -> list[DiscoveredDevice]:
    found: dict[int, DiscoveredDevice] = {}
    for data in replies:
        try:
            device_id, _stamp, checksum = crypto.parse_header(data)
        except crypto.MiioProtocolError:
            continue
        if device_id in (0, 0xFFFFFFFF):
            continue
        token = None
        if len(data) >= crypto.HEADER_LENGTH:
            if checksum not in (b"\x00" * 16, b"\xff" * 16):
                token = checksum.hex()
        # The source address is filled in by discover(); this helper is also
        # used by tests with synthetic packets.
        found[device_id] = found.get(device_id) or DiscoveredDevice(
            host="", device_id=device_id, token=token
        )
    return list(found.values())


async def discover(duration: float = 3.0, rounds: int = 3) -> list[DiscoveredDevice]:
    # Keep the address with each reply; devices_from_replies intentionally
    # ignores it because packet layout is what tests assert on.
    raw = await asyncio.to_thread(_collect_with_addresses, duration, rounds)
    found: dict[int, DiscoveredDevice] = {}
    for data, host in raw:
        try:
            device_id, _stamp, checksum = crypto.parse_header(data)
        except crypto.MiioProtocolError:
            continue
        if device_id in (0, 0xFFFFFFFF):
            continue
        token = None
        if checksum not in (b"\x00" * 16, b"\xff" * 16):
            token = checksum.hex()
        found.setdefault(
            device_id, DiscoveredDevice(host=host, device_id=device_id, token=token)
        )
    return sorted(found.values(), key=lambda item: item.host)


def _collect_with_addresses(duration: float, rounds: int) -> list[tuple[bytes, str]]:
    replies: list[tuple[bytes, str]] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    try:
        for _ in range(rounds):
            try:
                sock.sendto(crypto.HELLO, ("255.255.255.255", crypto.PORT))
            except OSError:
                pass
            end = duration / rounds
            waited = 0.0
            while waited < end:
                try:
                    data, addr = sock.recvfrom(2048)
                except TimeoutError:
                    waited += 0.5
                    continue
                except OSError:
                    break
                waited += 0.1
                if data:
                    replies.append((data, addr[0]))
    finally:
        sock.close()
    return replies
