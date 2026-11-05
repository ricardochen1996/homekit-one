"""Provider plugin registry.

Built-in providers register themselves in :mod:`homekit_one.providers.builtin`.
Third-party packages can publish a ``homekit_one.providers`` entry point whose
value is either a :class:`ProviderPlugin` or a zero-argument callable returning
one. The plugin's ``factory`` receives the loaded config, the secrets store,
the state directory and the LAN device registry, and returns a Provider.

A plugin that wants a generated setup form declares ``setup_fields``; the web
UI turns those into a credential form that writes ``secrets.json`` and flips the
provider's ``enabled`` flag. Built-in providers keep their richer custom cards.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from homekit_one.config import Config
from homekit_one.providers.base import Provider
from homekit_one.registry import DeviceRegistry
from homekit_one.secrets import SecretsStore

_LOGGER = logging.getLogger(__name__)

PluginFactory = Callable[[Config, SecretsStore, Path, DeviceRegistry], Provider]
PluginEnabled = Callable[[Config, DeviceRegistry | None], bool]


@dataclass(frozen=True)
class SetupField:
    key: str
    label: str
    placeholder: str = ""
    secret: bool = False
    required: bool = False
    default: str = ""


@dataclass(frozen=True)
class ProviderPlugin:
    name: str
    label: str
    factory: PluginFactory
    config_attr: str | None = None
    description: str = ""
    hint: str = ""
    setup_fields: tuple[SetupField, ...] = ()
    builtin: bool = False
    enabled: PluginEnabled | None = None

    def section(self, config: Config) -> Any:
        return getattr(config, self.config_attr, None) if self.config_attr else None

    def is_enabled(
        self, config: Config, registry: DeviceRegistry | None = None
    ) -> bool:
        if self.enabled is not None:
            return bool(self.enabled(config, registry))
        section = self.section(config)
        if section is not None:
            return bool(getattr(section, "enabled", False))
        raw = (config.provider_data or {}).get(self.name, {})
        return bool(raw.get("enabled", False))


_REGISTRY: dict[str, ProviderPlugin] = {}
_LOADED = False


def register(plugin: ProviderPlugin) -> ProviderPlugin:
    """Register or replace a provider plugin."""
    _REGISTRY[plugin.name] = plugin
    return plugin


def load_plugins() -> None:
    """Import built-ins and load third-party entry points exactly once."""
    global _LOADED
    if _LOADED:
        return
    from homekit_one.providers import builtin  # noqa: F401 - registers plugins

    try:
        entry_points = metadata.entry_points(group="homekit_one.providers")
    except TypeError:  # pragma: no cover - Python < 3.10 fallback
        entry_points = metadata.entry_points().get("homekit_one.providers", [])
    for entry_point in entry_points:
        try:
            loaded = entry_point.load()
            plugin = loaded() if callable(loaded) and not isinstance(loaded, ProviderPlugin) else loaded
            if isinstance(plugin, ProviderPlugin):
                register(plugin)
            else:
                _LOGGER.warning(
                    "provider entry point %s did not return a ProviderPlugin",
                    entry_point.name,
                )
        except Exception:  # noqa: BLE001 - one bad plugin must not stop startup
            _LOGGER.exception("failed to load provider plugin %s", entry_point.name)
    _LOADED = True


def plugins() -> list[ProviderPlugin]:
    load_plugins()
    return [plugin for _, plugin in sorted(_REGISTRY.items())]


def get_plugin(name: str) -> ProviderPlugin | None:
    load_plugins()
    return _REGISTRY.get(name)


def provider_labels() -> dict[str, str]:
    return {plugin.name: plugin.label for plugin in plugins()}


def build_providers(
    config: Config,
    secrets: SecretsStore,
    state_dir: Path,
    registry: DeviceRegistry | None = None,
) -> list[Provider]:
    providers: list[Provider] = []
    for plugin in plugins():
        if not plugin.is_enabled(config, registry):
            continue
        try:
            provider = plugin.factory(config, secrets, state_dir, registry)
        except Exception:  # noqa: BLE001 - isolate plugin construction failures
            _LOGGER.exception("provider %s failed to initialize", plugin.name)
            continue
        if provider is not None:
            providers.append(provider)
    return providers
