"""RSA/MD5 helpers for TCL+ (China) login.

Ported from the Apache-2.0 project ndwzy/tcl-ha
(custom_components/tcl/core/crypto.py). The RSA public key is embedded in the
TCL+ mobile app; it is not a user secret.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import urllib.parse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from homeone.providers.tcl_plus.constants import PUBLIC_KEY_LOGIN_SDK

_PUBLIC_KEY = serialization.load_der_public_key(
    base64.b64decode(PUBLIC_KEY_LOGIN_SDK)
)
_KEY_SIZE = 128  # 1024 bit
_BLOCK_SIZE = _KEY_SIZE - 11  # PKCS1 v1.5: max 117 bytes/block


def md5_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _rsa_encrypt(plaintext: bytes) -> bytes:
    result = b""
    offset = 0
    while offset < len(plaintext):
        block = plaintext[offset : offset + _BLOCK_SIZE]
        result += _PUBLIC_KEY.encrypt(block, padding.PKCS1v15())
        offset += _BLOCK_SIZE
    return result


def encrypt_url_params(params: dict) -> str:
    parts = []
    for key, value in params.items():
        encrypted = _rsa_encrypt(str(value).encode("utf-8"))
        encoded = base64.b64encode(encrypted).decode("ascii")
        parts.append(f"{key}={urllib.parse.quote(encoded, safe='')}")
    return "&".join(parts)


def encrypt_body(params: dict) -> bytes:
    payload = json.dumps(
        params, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return base64.b64encode(_rsa_encrypt(payload))


def generate_sign(params: dict) -> str:
    sign_str = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    sign_str += PUBLIC_KEY_LOGIN_SDK
    return md5_hash(sign_str)


def generate_nonce() -> str:
    return "02" + str(random.randint(0, 0x98967F))
