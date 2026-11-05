"""Import device tokens from a Xiaomi account.

Two paths are supported:

* OAuth (recommended): reuse the token saved by ``homekit-one mijia-oauth-login``
  or the web setup page. No password is needed and two-factor accounts work.
* Legacy ``micloud`` password login: kept as a fallback for older accounts.

Devices that the cloud reports without a LAN token still need manual IP/token
entry in the web UI.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


async def import_devices_from_oauth(client: Any) -> tuple[list[dict[str, Any]], int]:
    """Return ``(importable_devices, total_seen)`` from an OAuth cloud client."""
    records = await client.get_device_records()
    result: list[dict[str, Any]] = []
    for item in records:
        token = item.get("token") or item.get("local_token")
        did = item.get("did")
        if not token or not did:
            continue
        result.append(
            {
                "id": str(did),
                "name": item.get("name") or str(did),
                "model": item.get("model") or "",
                "host": item.get("localip") or item.get("local_ip") or "",
                "token": str(token),
            }
        )
    _LOGGER.info(
        "从小米 OAuth 设备列表导入 %d/%d 台带 token 的设备",
        len(result),
        len(records),
    )
    return result, len(records)


class TokenImportError(RuntimeError):
    pass


def import_devices(username: str, password: str, country: str = "cn") -> list[dict[str, Any]]:
    try:
        from micloud import MiCloud
    except ImportError as exc:  # pragma: no cover - dependency is in pyproject
        raise TokenImportError("未安装 micloud，请先执行 uv pip install micloud") from exc

    cloud = MiCloud(username, password)
    try:
        logged_in = cloud.login()
    except Exception as exc:  # noqa: BLE001 - micloud raises several types
        raise TokenImportError(f"小米账号登录失败: {exc}") from exc
    if not logged_in:
        raise TokenImportError("小米账号登录失败：请检查账号密码或两步验证")

    try:
        devices = cloud.get_devices(country=country) or []
    except Exception as exc:  # noqa: BLE001
        raise TokenImportError(f"读取小米设备列表失败: {exc}") from exc

    result: list[dict[str, Any]] = []
    for item in devices:
        token = item.get("token")
        did = item.get("did")
        if not token or not did:
            continue
        result.append(
            {
                "id": str(did),
                "name": item.get("name") or str(did),
                "model": item.get("model") or "",
                "host": item.get("localip") or "",
                "token": token,
            }
        )
    _LOGGER.info("从小米云导入 %d 台带 token 的设备", len(result))
    return result
