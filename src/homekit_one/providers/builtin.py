"""Built-in provider plugins."""

from __future__ import annotations

from homekit_one.providers.mijia.provider import MijiaProvider
from homekit_one.providers.mijia_cloud.provider import MijiaCloudProvider
from homekit_one.providers.plugins import ProviderPlugin, register
from homekit_one.providers.treeow.provider import TreeowProvider
from homekit_one.registry import DeviceRegistry


def _mijia_enabled(config, registry) -> bool:
    # Registry entries alone no longer force the LAN provider on: the web
    # setup page sets enabled=true when a device is added or imported.
    return bool(config.mijia.enabled)


register(
    ProviderPlugin(
        name="mijia",
        label="米家局域网",
        config_attr="mijia",
        builtin=True,
        description="局域网 miIO / MIoT Wi-Fi 设备。",
        factory=lambda config, secrets, state_dir, registry: MijiaProvider(
            config.mijia,
            secrets,
            state_dir,
            registry or DeviceRegistry(state_dir / "devices.json"),
        ),
        enabled=_mijia_enabled,
    )
)

register(
    ProviderPlugin(
        name="mijia_cloud",
        label="米家云",
        config_attr="mijia_cloud",
        builtin=True,
        description="小米云 MIoT-Spec 设备，覆盖中枢子设备和更多米家品类。",
        factory=lambda config, secrets, state_dir, registry: MijiaCloudProvider(
            config.mijia_cloud, secrets, state_dir
        ),
    )
)

register(
    ProviderPlugin(
        name="treeow",
        label="树新风 Treeow",
        config_attr="treeow",
        builtin=True,
        description="Treeow Home 云净化器。",
        factory=lambda config, secrets, state_dir, registry: TreeowProvider(
            config.treeow, secrets, state_dir
        ),
    )
)
