from __future__ import annotations

import tomllib

import pytest

from homeone.config import ConfigError, load_config, set_provider_enabled


def test_empty_environment_uses_fake_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEONE_CONFIG", str(tmp_path / "missing.toml"))
    config = load_config()
    assert config.fake.enabled is True
    assert config.has_real_provider is False
    assert config.gateway.web_port == 8080


def test_real_provider_disables_fake_by_default(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[providers.tcl]
enabled = true
username = "u"
password = "p"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOMEONE_CONFIG", str(path))
    config = load_config()
    assert config.tcl.enabled is True
    assert config.fake.enabled is False


def test_invalid_pincode_rejected(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[gateway]\nhomekit_pincode = "1234"\n', encoding="utf-8")
    monkeypatch.setenv("HOMEONE_CONFIG", str(path))
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
    monkeypatch.setenv("HOMEONE_CONFIG", str(path))
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
