from __future__ import annotations

import tomllib

import pytest

from homekit_one.config import (
    ConfigError,
    default_config_path,
    default_state_dir,
    load_config,
    set_provider_devices,
    set_provider_enabled,
)


def test_empty_environment_uses_fake_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEKIT_ONE_CONFIG", str(tmp_path / "missing.toml"))
    config = load_config()
    assert config.fake.enabled is True
    assert config.has_real_provider is False
    assert config.gateway.web_port == 8080


def test_real_provider_disables_fake_by_default(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[providers.treeow]
enabled = true
account = "a"
password = "p"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOMEKIT_ONE_CONFIG", str(path))
    config = load_config()
    assert config.treeow.enabled is True
    assert config.fake.enabled is False


def test_invalid_pincode_rejected(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[gateway]\nhomekit_pincode = "1234"\n', encoding="utf-8")
    monkeypatch.setenv("HOMEKIT_ONE_CONFIG", str(path))
    with pytest.raises(ConfigError):
        load_config()


def test_mijia_device_and_poll_intervals(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[gateway.poll_intervals]
mijia = 2

[[providers.mijia.devices]]
id = "123"
name = "净化器"
model = "zhimi.airpurifier.rma1"
host = "192.168.1.2"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOMEKIT_ONE_CONFIG", str(path))
    config = load_config()
    assert config.gateway.poll_intervals["mijia"] == 2
    assert config.mijia.devices[0].id == "123"
    assert config.mijia.devices[0].name == "净化器"


def test_set_provider_enabled_preserves_other_sections(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[gateway]\nname = "X"\n', encoding="utf-8")
    target = set_provider_enabled(path, "treeow", True)
    data = tomllib.loads(target.read_text("utf-8"))
    assert data["gateway"]["name"] == "X"
    assert data["providers"]["treeow"]["enabled"] is True


def test_set_provider_devices_writes_and_clears_allow_list(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[providers.treeow]\nenabled = true\n', encoding="utf-8")
    target = set_provider_devices(path, "treeow", ["T2-1", "T2-2"])
    data = tomllib.loads(target.read_text("utf-8"))
    assert data["providers"]["treeow"]["devices"] == ["T2-1", "T2-2"]
    assert data["providers"]["treeow"]["enabled"] is True

    set_provider_devices(path, "treeow", [])
    data = tomllib.loads(path.read_text("utf-8"))
    assert "devices" not in data["providers"]["treeow"]


def test_old_default_gateway_name_follows_rename(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[gateway]\nname = "HomeOne"\n', encoding="utf-8")
    monkeypatch.setenv("HOMEKIT_ONE_CONFIG", str(path))
    assert load_config().gateway.name == "HomeKit One"

    path.write_text('[gateway]\nname = "客厅网关"\n', encoding="utf-8")
    assert load_config().gateway.name == "客厅网关"


def test_legacy_config_and_state_paths_are_used_until_migrated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    old_config = home / ".config" / "homeone" / "config.toml"
    old_state = home / "Library" / "Application Support" / "HomeOne"
    old_config.parent.mkdir(parents=True)
    old_state.mkdir(parents=True)
    old_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HOMEKIT_ONE_CONFIG", raising=False)
    monkeypatch.delenv("HOMEONE_CONFIG", raising=False)

    assert default_config_path() == old_config
    assert default_state_dir() == old_state
    config = load_config()
    assert config.path == old_config
    assert config.state_dir == old_state


def test_legacy_config_env_is_still_honored(tmp_path, monkeypatch):
    path = tmp_path / "legacy.toml"
    path.write_text("", encoding="utf-8")
    monkeypatch.delenv("HOMEKIT_ONE_CONFIG", raising=False)
    monkeypatch.setenv("HOMEONE_CONFIG", str(path))
    assert default_config_path() == path
