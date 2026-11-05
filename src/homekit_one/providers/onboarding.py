"""One-shot provider checks used by the setup UI and ``homekit-one doctor``.

The running gateway keeps providers alive and polls them continuously. These
helpers deliberately build a *throwaway* provider from the saved configuration,
log in, list devices, read a handful of states, and return a plain report. They
never mutate config or secrets, so a failed check cannot disturb the gateway.

The report is intentionally UI-shaped: a summary, an actionable hint, and one
row per device. Keeping it provider-neutral means the setup page and the CLI
can render every brand the same way.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homekit_one.config import Config
from homekit_one.core.model import Device
from homekit_one.providers.base import Provider
from homekit_one.providers.mijia.client import MiioClient
from homekit_one.providers.mijia.models import MODELS
from homekit_one.providers.mijia.provider import MijiaProvider
from homekit_one.providers.plugins import get_plugin, provider_labels
from homekit_one.registry import DeviceRegistry
from homekit_one.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)

PROVIDER_LABELS = provider_labels()

# Reading a state is what actually proves a token (handshake only proves the IP
# is alive). Five devices is enough to catch a bad token without turning the
# setup form into a full poll cycle.
MAX_STATE_READS = 10
PROBE_TIMEOUT = 45.0
DEVICE_TIMEOUT = 12.0


@dataclass
class DeviceReport:
    id: str
    name: str
    kind: str = ""
    model: str = ""
    online: bool = False
    reachable: bool | None = None
    error: str = ""
    capabilities: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderReport:
    provider: str
    label: str
    ok: bool
    summary: str
    hint: str = ""
    devices: list[DeviceReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))

    @property
    def level(self) -> str:
        """CSS-friendly state: ok / warn / bad."""
        if not self.ok:
            return "bad"
        if not self.devices or self.warnings:
            return "warn"
        return "ok"

    @property
    def reachable(self) -> int:
        return sum(1 for device in self.devices if device.reachable)


def _failure(
    provider: str,
    label: str,
    summary: str,
    exc: BaseException | str | None = None,
    *,
    hint: str = "",
) -> ProviderReport:
    detail = _short(exc) if exc is not None else ""
    return ProviderReport(
        provider=provider,
        label=label,
        ok=False,
        summary=summary,
        hint=hint or _hint_for(provider, detail),
        warnings=[detail] if detail else [],
    )


async def probe_provider(
    name: str,
    config: Config,
    secrets: SecretsStore,
    state_dir: Path,
    registry: DeviceRegistry | None = None,
) -> ProviderReport:
    """Log in with the *saved* credentials and report what can be added."""
    plugin = get_plugin(name)
    if plugin is None:
        return _failure(name, name, f"未知 provider: {name}", hint="可选值见配置页")
    label = plugin.label

    try:
        provider = _build_provider(name, config, secrets, state_dir, registry)
    except Exception as exc:  # noqa: BLE001 - construction may read a bad config
        return _failure(name, label, f"{label} 初始化失败", exc)

    if _missing_credentials(provider):
        return _failure(
            name,
            label,
            f"{label} 还没有保存凭据",
            hint=_missing_credentials_hint(name),
        )

    try:
        try:
            await asyncio.wait_for(provider.start(), timeout=PROBE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            return _failure(name, label, f"{label} 登录失败", exc)
        last_error = getattr(provider, "last_error", None)
        if last_error:
            return _failure(name, label, f"{label} 登录失败", last_error)

        try:
            devices = await asyncio.wait_for(
                provider.list_devices(), timeout=PROBE_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001
            return _failure(name, label, f"{label} 读取设备列表失败", exc)

        warnings = _config_warnings(name, config, devices)
        reports = [_device_report(device) for device in devices]
        if name == "mijia":
            warnings.extend(_mijia_entry_warnings(provider, devices))

        if not devices:
            summary = (
                f"{label} 没有发现可接入设备"
                if name == "mijia"
                else f"{label} 登录成功，但没有发现可接入设备"
            )
            return ProviderReport(
                provider=name,
                label=label,
                ok=True,
                summary=summary,
                hint=_empty_hint(name),
                warnings=warnings,
            )

        await asyncio.gather(
            *(
                _fill_state(provider, device, report)
                for device, report in zip(
                    devices[:MAX_STATE_READS], reports[:MAX_STATE_READS], strict=False
                )
            ),
            return_exceptions=True,
        )
        failed = [report for report in reports if report.reachable is False]
        if failed:
            names = "、".join(report.name for report in failed[:3])
            more = f" 等 {len(failed)} 台" if len(failed) > 3 else ""
            warnings.append(f"{names}{more} 登录成功但状态读取失败")
        reachable = sum(1 for report in reports if report.reachable)
        summary = f"{label} 验证成功：发现 {len(devices)} 台设备"
        if len(devices) > MAX_STATE_READS:
            summary += f"，已读取前 {MAX_STATE_READS} 台状态"
        elif reachable:
            summary += f"，{reachable} 台状态读取正常"
        return ProviderReport(
            provider=name,
            label=label,
            ok=True,
            summary=summary,
            hint=_success_hint(name, failed),
            devices=reports,
            warnings=warnings,
        )
    finally:
        try:
            await provider.stop()
        except Exception:  # noqa: BLE001 - a probe must never raise on cleanup
            _LOGGER.debug("probe cleanup failed for %s", name, exc_info=True)


async def probe_mijia_device(
    device_id: str,
    host: str,
    token: str,
    model: str,
    *,
    name: str = "",
) -> ProviderReport:
    """Check one LAN device right after it is added in the web UI."""
    label = PROVIDER_LABELS["mijia"]
    display = name or device_id or host
    spec = MODELS.get(model)
    if spec is None:
        return _failure(
            "mijia",
            label,
            f"型号 {model or '(空)'} 暂不支持",
            hint="支持型号：" + "、".join(sorted(MODELS)) + "。其他型号需要先补属性映射。",
        )
    if not host or not token:
        return _failure(
            "mijia",
            label,
            f"{display} 缺少 IP 或 token",
            hint="token 是 32 位十六进制；可在米家 App 的账号信息、路由器后台或小米云导入中获取。",
        )

    client = MiioClient(host=host, token=token, timeout=3.0)
    try:
        raw = await asyncio.wait_for(
            client.get_properties(spec.properties()), timeout=DEVICE_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001 - wrong token and offline look similar
        return _failure(
            "mijia",
            label,
            f"{display} 连接失败",
            exc,
            hint="确认 IP 没有被 DHCP 改变、Mac 与设备在同一网段；如果 IP 正确，通常是 token 不对。",
        )

    values = spec.decode(raw)
    if not values:
        return _failure(
            "mijia",
            label,
            f"{display} 没有返回可识别属性",
            hint="设备在线但属性为空，通常是 token 不正确或固件属性表不同。",
        )

    report = ProviderReport(
        provider="mijia",
        label=label,
        ok=True,
        summary=f"{display} 验证成功：token 与 IP 可用",
        hint="重启网关后即可在「家庭」App 中看到它。",
        devices=[
            DeviceReport(
                id=device_id or str(client.device_id or ""),
                name=display,
                kind=str(spec.kind),
                model=model,
                online=True,
                reachable=True,
                capabilities=sorted(str(item) for item in spec.capabilities),
                state=_display_state(values),
            )
        ],
    )
    if client.device_id is not None and device_id and not _same_did(device_id, client.device_id):
        report.warnings.append(
            f"设备实际 did 是 {client.device_id}，与填写的 {device_id} 不一致"
            "（控制不受影响，建议改成实际 did）"
        )
    return report


def _build_provider(
    name: str,
    config: Config,
    secrets: SecretsStore,
    state_dir: Path,
    registry: DeviceRegistry | None,
) -> Provider:
    plugin = get_plugin(name)
    if plugin is None:
        raise ValueError(f"unknown provider {name}")
    return plugin.factory(config, secrets, state_dir, registry)


def _missing_credentials(provider: Provider) -> bool:
    # Cloud providers expose their transport as ``client``/``cloud``/``api``.
    for attr in ("client", "cloud", "api"):
        if hasattr(provider, attr):
            return getattr(provider, attr) is None
    return False


def _missing_credentials_hint(name: str) -> str:
    return {
        "mijia": "先在下方「米家设备」里扫描或手动添加一台设备。",
        "mijia_cloud": "先完成小米 OAuth 授权（推荐），或填写旧版账号密码。",
        "treeow": "填写 Treeow Home App 的账号和密码。",
    }.get(name, "先保存凭据。")


def _empty_hint(name: str) -> str:
    return {
        "mijia": "设备缺少 IP/token，或型号不在支持列表；可先点「扫描局域网」。",
        "mijia_cloud": "确认区域与设备注册地一致；支持灯、插座、开关、净化器、风扇、空调、温控、温湿度传感器、摄像头、扫地机、宠物喂食器/饮水机和音箱。",
        "treeow": "确认设备已绑定到 Treeow Home App 的当前家庭。",
    }.get(name, "确认账号下有受支持的设备。")


def _success_hint(name: str, failed: list[DeviceReport]) -> str:
    if failed:
        return "部分设备状态读取失败，可稍后点「验证」重试；IP/token 问题会在日志里给出原因。"
    return {
        "mijia": "重启网关后生效。",
        "mijia_cloud": (
            "重启网关后生效；中枢子设备需要保持中枢在线。"
            "米家 Wi-Fi 设备可在下方「米家局域网设备」里导入 token。"
        ),
        "treeow": "重启网关后生效。",
    }.get(name, "重启网关后生效。")


def _hint_for(name: str, detail: str) -> str:
    text = detail.lower()
    if name == "mijia_cloud" and (
        "403" in text or "密码登录" in detail or "access denied" in text
    ):
        return (
            "小米已限制旧版密码登录。请回到配置页点「小米 OAuth 授权」，"
            "浏览器登录后把回调地址粘贴回来即可。"
        )
    if name == "mijia_cloud" and ("401" in text or "失效" in detail or "授权" in detail):
        return "OAuth 授权可能已过期，重新点「小米 OAuth 授权」即可。"
    if name == "treeow" and ("登录" in detail or "auth" in text or "password" in text):
        return "确认账号是 Treeow Home App 注册的手机号/账号，密码区分大小写；如果最近在 App 改过密码，请在配置页重新填写。"
    if "timeout" in text or "timed out" in text or "超时" in detail:
        return "网络请求超时：确认 Mac 能正常访问外网，或设备与 Mac 在同一网段。"
    if name == "mijia" and ("token" in text or "握手" in detail or "校验" in detail):
        return "米家局域网设备需要正确的 32 位 token；可先用「扫描局域网」确认 IP 和设备在线。"
    return "检查账号密码和网络；完整错误见「日志」页。"


def _config_warnings(
    name: str, config: Config, devices: list[Device]
) -> list[str]:
    plugin = get_plugin(name)
    section = getattr(config, plugin.config_attr, None) if plugin and plugin.config_attr else None
    allow = getattr(section, "devices", ()) if name != "mijia" else ()
    if allow:
        return [
            f"配置里设置了 devices 白名单（{len(allow)} 台），"
            f"上面列出的是账号下全部 {len(devices)} 台；网关只会接入白名单内的设备。"
        ]
    return []


def _mijia_entry_warnings(provider: Provider, devices: list[Device]) -> list[str]:
    warnings: list[str] = []
    if not isinstance(provider, MijiaProvider):
        return warnings
    entries = provider._merged_entries()
    unsupported = [
        str(entry.get("model") or entry.get("id"))
        for entry in entries
        if str(entry.get("model") or "") not in MODELS
    ]
    missing = [
        str(entry.get("id"))
        for entry in entries
        if entry.get("model") in MODELS
        and (
            not entry.get("host")
            or not provider._token(str(entry["id"]), entry.get("token"))
        )
    ]
    if unsupported:
        warnings.append(
            f"{len(unsupported)} 台型号不在局域网支持列表；"
            "如果已启用米家云，会由云端 provider 尝试接入"
        )
    if missing:
        warnings.append(
            "缺少 IP 或 token，已跳过：" + "、".join(missing[:5])
            + (f" 等 {len(missing)} 台" if len(missing) > 5 else "")
        )
    if not devices and not entries:
        warnings.append("还没有添加任何米家局域网设备")
    return warnings


def _device_report(device: Device) -> DeviceReport:
    meta = device.meta
    external_id = str(
        meta.get("did")
        or meta.get("device_id")
        or meta.get("serial")
        or device.id
    )
    return DeviceReport(
        id=external_id,
        name=device.name,
        kind=str(device.kind),
        model=device.model,
        online=bool(device.online),
        capabilities=sorted(str(item) for item in device.capabilities),
    )


async def _fill_state(
    provider: Provider, device: Device, report: DeviceReport
) -> None:
    try:
        values = await asyncio.wait_for(
            provider.refresh(device), timeout=DEVICE_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001 - reported per device
        report.reachable = False
        report.error = _short(exc)
        return
    report.reachable = True
    report.online = True
    report.state = _display_state(values)


def _display_state(values: dict[Any, Any]) -> dict[str, Any]:
    return {
        str(key): _display_value(value)
        for key, value in sorted(values.items(), key=lambda item: str(item[0]))
    }


def _display_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "开" if value else "关"
    if isinstance(value, float):
        return round(value, 1)
    return value


def _short(exc: BaseException | str, limit: int = 280) -> str:
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__ if isinstance(exc, BaseException) else "未知错误"
    return text[:limit]


def _same_did(configured: str, actual: int) -> bool:
    value = str(configured).strip().lower()
    if value.startswith("0x"):
        try:
            return int(value, 16) == actual
        except ValueError:
            return False
    try:
        return int(value) == actual
    except ValueError:
        return False
