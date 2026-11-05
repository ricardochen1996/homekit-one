from __future__ import annotations

from homekit_one.secrets import SecretsStore


def test_resolve_precedence(tmp_path, monkeypatch):
    store = SecretsStore(tmp_path / "secrets.json")
    store.set_many("treeow", {"account": "from-secrets"})
    monkeypatch.setenv("HOMEKIT_ONE_TREOW_ACCOUNT", "from-env")
    assert (
        store.resolve("treeow", "account", "from-config", "HOMEKIT_ONE_TREOW_ACCOUNT")
        == "from-env"
    )

    monkeypatch.delenv("HOMEKIT_ONE_TREOW_ACCOUNT")
    assert (
        store.resolve("treeow", "account", "from-config", "HOMEKIT_ONE_TREOW_ACCOUNT")
        == "from-secrets"
    )

    store.delete("treeow", "account")
    assert (
        store.resolve("treeow", "account", "from-config", "HOMEKIT_ONE_TREOW_ACCOUNT")
        == "from-config"
    )


def test_delete_section(tmp_path):
    store = SecretsStore(tmp_path / "secrets.json")
    store.set_many("mijia_oauth", {"access_token": "a"})
    store.delete_section("mijia_oauth")
    assert store.section("mijia_oauth") == {}
