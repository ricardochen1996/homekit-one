"""Import device tokens from a Xiaomi account.

micloud uses the same web login as the Mi Home account page. Accounts with
two-factor authentication may reject this flow; manual IP + token entry in the
web UI remains the fallback.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


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
