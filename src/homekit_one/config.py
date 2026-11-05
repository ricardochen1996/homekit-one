"""TOML configuration loading and validation."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("~/.config/homekit-one/config.toml")
DEFAULT_STATE_DIR = Path("~/Library/Application Support/HomeKit One")
# Pre-rename locations. Keep using them when the new path does not exist yet,
# so an existing install keeps its credentials and HomeKit pairing.
LEGACY_CONFIG_PATH = Path("~/.config/homeone/config.toml")
LEGACY_STATE_DIR = Path("~/Library/Application Support/HomeOne")
PINCODE_RE = re.compile(r"^\d{3}-\d{2}-\d{3}$")


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return None


def default_state_dir() -> Path:
    new = DEFAULT_STATE_DIR.expanduser()
    legacy = LEGACY_STATE_DIR.expanduser()
    if not new.exists() and legacy.exists():
        return legacy
    return new

DEFAULT_POLL_INTERVALS = {"mijia": 5.0, "treeow": 8.0, "fake": 30.0}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MijiaDeviceConfig:
    id: str
    name: str
    model: str
    host: str
    token: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class MijiaConfig:
    enabled: bool = False
    poll_interval: float = 5.0
    devices: tuple[MijiaDeviceConfig, ...] = ()


@dataclass(frozen=True)
class MijiaCloudConfig:
    enabled: bool = False
    poll_interval: float = 15.0
    username: str | None = None
    password: str | None = None
    country: str = "cn"
    devices: tuple[str, ...] = ()


@dataclass(frozen=True)
class TreeowConfig:
    enabled: bool = False
    poll_interval: float = 8.0
    account: str | None = None
    password: str | None = None
    devices: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeConfig:
    enabled: bool = True


@dataclass(frozen=True)
class GatewayConfig:
    name: str = "HomeKit One"
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    homekit_port: int = 51234
    homekit_pincode: str | None = None
    # Optional override; by default the LAN address is auto-detected so a VPN
    # (utun) address is not advertised to HomeKit clients.
    homekit_address: str | None = None
    # minimal = native services + readings; extra switches only for lights
    #           and thermostats (lamp / electric blanket)
    # auto    = the most useful extra switches for every device
    # all     = every mapped extra switch
    # none    = no extra switches at all
    extra_switches: str = "minimal"
    log_level: str = "INFO"
    state_dir: Path = field(default_factory=default_state_dir)
    poll_intervals: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_POLL_INTERVALS))


@dataclass(frozen=True)
class Config:
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    mijia: MijiaConfig = field(default_factory=MijiaConfig)
    mijia_cloud: MijiaCloudConfig = field(default_factory=MijiaCloudConfig)
    treeow: TreeowConfig = field(default_factory=TreeowConfig)
    fake: FakeConfig = field(default_factory=FakeConfig)
    path: Path | None = None
    # Raw [providers.*] sections, used by external plugins that do not have a
    # built-in dataclass in this module.
    provider_data: dict[str, Any] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return self.gateway.state_dir

    @property
    def has_real_provider(self) -> bool:
        return (
            self.mijia.enabled
            or self.mijia_cloud.enabled
            or self.treeow.enabled
        )


def _get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _as_bool(value: Any, default: bool) -> bool:
    return bool(value) if value is not None else default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def load_config(path: str | Path | None = None) -> Config:
    raw: dict[str, Any] = {}
    config_path: Path | None = None
    candidate = (
        Path(path).expanduser() if path else default_config_path()
    )
    if candidate.exists():
        config_path = candidate
        try:
            raw = tomllib.loads(candidate.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"无法读取配置文件 {candidate}: {exc}") from exc
    elif path:
        raise ConfigError(f"配置文件不存在: {candidate}")

    gateway_raw = _get(raw, "gateway", default={}) or {}
    state_dir = Path(
        os.path.expanduser(str(gateway_raw.get("state_dir", default_state_dir())))
    )
    poll_raw = gateway_raw.get("poll_intervals") or {}
    poll_intervals = dict(DEFAULT_POLL_INTERVALS)
    for key, value in poll_raw.items():
        poll_intervals[str(key)] = max(1.0, _as_float(value, poll_intervals.get(key, 10.0)))

    pincode = gateway_raw.get("homekit_pincode")
    if pincode is not None and not PINCODE_RE.match(str(pincode)):
        raise ConfigError("homekit_pincode 必须是 xxx-xx-xxx 格式，例如 031-45-678")

    extra_switches = str(gateway_raw.get("extra_switches", "minimal")).lower()
    if extra_switches not in ("minimal", "auto", "all", "none"):
        extra_switches = "minimal"
    gateway_name = str(gateway_raw.get("name", "HomeKit One"))
    if gateway_name == "HomeOne":
        # Old default; follow the rename without overriding a custom name.
        gateway_name = "HomeKit One"
    gateway = GatewayConfig(
        name=gateway_name,
        web_host=str(gateway_raw.get("web_host", "127.0.0.1")),
        web_port=int(gateway_raw.get("web_port", 8080)),
        homekit_port=int(gateway_raw.get("homekit_port", 51234)),
        homekit_pincode=str(pincode) if pincode else None,
        homekit_address=str(gateway_raw["homekit_address"])
        if gateway_raw.get("homekit_address")
        else None,
        extra_switches=extra_switches,
        log_level=str(gateway_raw.get("log_level", "INFO")),
        state_dir=state_dir,
        poll_intervals=poll_intervals,
    )

    mijia_raw = _get(raw, "providers", "mijia", default={}) or {}
    mijia_devices: list[MijiaDeviceConfig] = []
    for item in mijia_raw.get("devices") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mijia_devices.append(
            MijiaDeviceConfig(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                model=str(item.get("model") or ""),
                host=str(item.get("host") or ""),
                token=item.get("token"),
                kind=item.get("kind"),
            )
        )
    mijia = MijiaConfig(
        enabled=_as_bool(mijia_raw.get("enabled"), False),
        poll_interval=max(1.0, _as_float(mijia_raw.get("poll_interval"), 5.0)),
        devices=tuple(mijia_devices),
    )

    cloud_raw = _get(raw, "providers", "mijia_cloud", default={}) or {}
    mijia_cloud = MijiaCloudConfig(
        enabled=_as_bool(cloud_raw.get("enabled"), False),
        poll_interval=max(1.0, _as_float(cloud_raw.get("poll_interval"), 15.0)),
        username=cloud_raw.get("username")
        or _env("HOMEKIT_ONE_MIJIA_CLOUD_USERNAME", "HONE_MIJIA_CLOUD_USERNAME"),
        password=cloud_raw.get("password")
        or _env("HOMEKIT_ONE_MIJIA_CLOUD_PASSWORD", "HONE_MIJIA_CLOUD_PASSWORD"),
        country=str(cloud_raw.get("country") or "cn").lower(),
        devices=_as_tuple(cloud_raw.get("devices")),
    )

    treeow_raw = _get(raw, "providers", "treeow", default={}) or {}
    treeow = TreeowConfig(
        enabled=_as_bool(treeow_raw.get("enabled"), False),
        poll_interval=max(1.0, _as_float(treeow_raw.get("poll_interval"), 8.0)),
        account=treeow_raw.get("account") or _env("HOMEKIT_ONE_TREOW_ACCOUNT", "HONE_TREOW_ACCOUNT"),
        password=treeow_raw.get("password") or _env("HOMEKIT_ONE_TREOW_PASSWORD", "HONE_TREOW_PASSWORD"),
        devices=_as_tuple(treeow_raw.get("devices")),
    )

    fake_raw = _get(raw, "providers", "fake", default={}) or {}
    # With no explicit setting, only show demo devices when no real provider
    # is configured. An explicit enabled = true always wins.
    fake_default = not (
        mijia.enabled
        or mijia_cloud.enabled
        or treeow.enabled
    )
    fake = FakeConfig(enabled=_as_bool(fake_raw.get("enabled"), fake_default))

    config = Config(
        gateway=gateway,
        mijia=mijia,
        mijia_cloud=mijia_cloud,
        treeow=treeow,
        fake=fake,
        path=config_path,
        provider_data=dict(raw.get("providers") or {}),
    )
    if not config.has_real_provider and config.path is not None and not fake.enabled:
        # A configured file that enables nothing is almost always a mistake;
        # keep the demo provider on so the gateway still starts usefully.
        config = replace(config, fake=FakeConfig(enabled=True))
    return config


def default_config_path() -> Path:
    env = _env("HOMEKIT_ONE_CONFIG", "HOMEONE_CONFIG")
    if env:
        return Path(env).expanduser()
    new = DEFAULT_CONFIG_PATH.expanduser()
    legacy = LEGACY_CONFIG_PATH.expanduser()
    if not new.exists() and legacy.exists():
        return legacy
    return new


def set_provider_enabled(path: str | Path | None, provider: str, enabled: bool = True) -> Path:
    """Persist an enabled flag without touching credentials.

    The web UI uses this after credentials are saved in secrets.json. Writing
    the file with tomli-w drops comments, so manual edits remain the nicer
    path for people who care about them.
    """
    import tomli_w

    target = Path(path).expanduser() if path else default_config_path()
    data: dict[str, Any] = {}
    if target.exists():
        try:
            data = tomllib.loads(target.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
    providers = data.setdefault("providers", {})
    section = providers.setdefault(provider, {})
    if isinstance(section, list):  # malformed; replace rather than crash
        section = {}
        providers[provider] = section
    section["enabled"] = bool(enabled)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomli_w.dumps(data), encoding="utf-8")
    return target


def set_provider_devices(
    path: str | Path | None, provider: str, devices: list[str]
) -> Path:
    """Persist the per-provider allow-list. An empty list means "all devices"."""
    import tomli_w

    target = Path(path).expanduser() if path else default_config_path()
    data: dict[str, Any] = {}
    if target.exists():
        try:
            data = tomllib.loads(target.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
    providers = data.setdefault("providers", {})
    section = providers.setdefault(provider, {})
    if isinstance(section, list):  # malformed; replace rather than crash
        section = {}
        providers[provider] = section
    if devices:
        section["devices"] = [str(item) for item in devices]
    else:
        section.pop("devices", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomli_w.dumps(data), encoding="utf-8")
    return target
