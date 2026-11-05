"""miIO LAN framing (UDP 54321).

Layout: 32-byte header + AES-128-CBC encrypted JSON.
  0..2 magic 0x2131, 2..4 length, 8..12 device id, 12..16 stamp,
  16..32 MD5 checksum. Key = MD5(token), IV = MD5(key + token).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"\x21\x31"
HEADER_LENGTH = 32
PORT = 54321
HELLO = MAGIC + b"\x00\x20" + b"\xff" * 28


class MiioProtocolError(RuntimeError):
    pass


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data).digest()


def token_bytes(token: str) -> bytes:
    cleaned = token.strip().lower().removeprefix("0x")
    if len(cleaned) != 32:
        raise MiioProtocolError("miIO token 必须是 32 位十六进制字符串")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise MiioProtocolError("miIO token 不是合法十六进制") from exc


class MiioCipher:
    def __init__(self, token: bytes) -> None:
        if len(token) != 16:
            raise MiioProtocolError("miIO token 必须是 16 字节")
        self.token = token
        self.key = _md5(token)
        self.iv = _md5(self.key + token)

    def encrypt(self, payload: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(payload) + padder.finalize()
        encryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, payload: bytes) -> bytes:
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        padded = decryptor.update(payload) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()


def parse_header(packet: bytes) -> tuple[int, int, bytes]:
    if len(packet) < HEADER_LENGTH or packet[:2] != MAGIC:
        raise MiioProtocolError("不是 miIO 数据包")
    length = int.from_bytes(packet[2:4], "big")
    if length < HEADER_LENGTH or length > len(packet):
        raise MiioProtocolError("miIO 数据包长度不合法")
    device_id = int.from_bytes(packet[8:12], "big")
    stamp = int.from_bytes(packet[12:16], "big")
    return device_id, stamp, packet[16:32]


def encode(payload: bytes, token: bytes, device_id: int, stamp: int) -> bytes:
    cipher = MiioCipher(token)
    encrypted = cipher.encrypt(payload)
    length = HEADER_LENGTH + len(encrypted)
    if length > 0xFFFF:
        raise MiioProtocolError("miIO 负载过长")
    header = bytearray(MAGIC)
    header += length.to_bytes(2, "big")
    header += b"\x00\x00\x00\x00"
    header += device_id.to_bytes(4, "big")
    header += stamp.to_bytes(4, "big")
    checksum = _md5(bytes(header) + token + encrypted)
    return bytes(header) + checksum + encrypted


def decode(packet: bytes, token: bytes) -> tuple[int, int, bytes]:
    device_id, stamp, checksum = parse_header(packet)
    encrypted = packet[HEADER_LENGTH : int.from_bytes(packet[2:4], "big")]
    if not encrypted:
        return device_id, stamp, b""
    expected = _md5(packet[:16] + token + encrypted)
    if checksum != expected:
        raise MiioProtocolError("token 校验失败（通常是 token 错误）")
    return device_id, stamp, MiioCipher(token).decrypt(encrypted)


def parse_payload(payload: bytes) -> Any:
    text = payload.rstrip(b"\x00 \r\n").decode("utf-8", errors="replace")
    # Some firmware pads junk after a NUL or emits ,, before a key.
    text = text.split("\x00", 1)[0].replace(',,"', ',"')
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MiioProtocolError(f"无法解析设备响应: {text[:120]}") from exc
