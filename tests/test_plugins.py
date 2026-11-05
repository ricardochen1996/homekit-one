from __future__ import annotations

from homekit_one.config import Config, GatewayConfig
from homekit_one.core.model import Device
from homekit_one.providers import plugins
from homekit_one.providers.plugins import ProviderPlugin, SetupField, build_providers
from homekit_one.secrets import SecretsStore


class _FakeProvider:
    name = "dummy_test"

    def __init__(self, config, secrets, state_dir, registry) -> None:
        self.config = config
        self.secrets = secrets
        self.state_dir = state_dir
        self.registry = registry

    async def list_devices(self) -> list[Device]:
        return []

    async def refresh(self, device: Device) -> dict:
        return {}

    async def execute(self, device: Device, command) -> None:
        return None


def test_builtin_plugins_are_registered():
    names = {plugin.name for plugin in plugins.plugins()}
    assert {"mijia", "mijia_cloud", "treeow"} <= names
    assert plugins.get_plugin("mijia").label == "米家局域网"


def test_external_plugin_builds_from_provider_data(tmp_path):
    plugin = ProviderPlugin(
        name="dummy_test",
        label="Dummy",
        factory=lambda config, secrets, state_dir, registry: _FakeProvider(
            config, secrets, state_dir, registry
        ),
        setup_fields=(SetupField("token", "Token", secret=True, required=True),),
    )
    plugins.register(plugin)
    try:
        config = Config(
            gateway=GatewayConfig(state_dir=tmp_path),
            path=tmp_path / "config.toml",
            provider_data={"dummy_test": {"enabled": True}},
        )
        secrets = SecretsStore(tmp_path / "secrets.json")
        built = build_providers(config, secrets, tmp_path, None)
        assert [provider.name for provider in built] == ["dummy_test"]
        assert plugin.is_enabled(config, None) is True
    finally:
        plugins._REGISTRY.pop("dummy_test", None)
