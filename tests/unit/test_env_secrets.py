"""Unit tests for EnvSecretsManager."""

from __future__ import annotations

from general_ludd.secrets.env import EnvSecretsManager


class TestEnvSecretsManager:
    def test_resolve_from_override(self):
        mgr = EnvSecretsManager(overrides={"MY_KEY": "secret123"})
        assert mgr.resolve("MY_KEY") == "secret123"

    def test_resolve_from_environ(self, monkeypatch):
        # S-1 fix: ambient-env resolution is now restricted to allowlisted
        # credential names (e.g. *_API_KEY). An allowlisted name still resolves
        # from os.environ when not overridden.
        monkeypatch.setenv("_TEST_ENV_API_KEY", "envval")
        mgr = EnvSecretsManager()
        assert mgr.resolve("_TEST_ENV_API_KEY") == "envval"

    def test_override_takes_precedence_over_environ(self, monkeypatch):
        monkeypatch.setenv("_TEST_ENV_API_KEY2", "envval")
        mgr = EnvSecretsManager(overrides={"_TEST_ENV_API_KEY2": "override"})
        assert mgr.resolve("_TEST_ENV_API_KEY2") == "override"

    def test_non_allowlisted_env_var_is_not_resolved(self, monkeypatch):
        # S-1: a non-credential ambient env var (e.g. GLUDD_PSK, PATH) must NOT
        # be resolvable from os.environ — fail-closed.
        monkeypatch.setenv("_TEST_PLAIN_VAR", "envval")
        mgr = EnvSecretsManager()
        assert mgr.resolve("_TEST_PLAIN_VAR") is None

    def test_gludd_psk_is_never_resolved_from_env(self, monkeypatch):
        # S-1: the PSK must never leak through the secrets manager.
        monkeypatch.setenv("GLUDD_PSK", "super-secret-psk")
        mgr = EnvSecretsManager()
        assert mgr.resolve("GLUDD_PSK") is None

    def test_explicit_allow_set_permits_env_resolution(self, monkeypatch):
        # A caller may vouch for a specific ambient name via the allow-set.
        monkeypatch.setenv("_TEST_VOUCHED", "envval")
        mgr = EnvSecretsManager(allow={"_TEST_VOUCHED"})
        assert mgr.resolve("_TEST_VOUCHED") == "envval"

    def test_resolve_missing_returns_none(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("NONEXISTENT_KEY_XYZ") is None

    def test_set_adds_override(self):
        mgr = EnvSecretsManager()
        mgr.set("NEW_KEY", "val")
        assert mgr.resolve("NEW_KEY") == "val"

    def test_list_aliases(self):
        mgr = EnvSecretsManager(overrides={"b": "2", "a": "1"})
        assert mgr.list_aliases() == ["a", "b"]

    def test_empty_manager(self):
        mgr = EnvSecretsManager()
        assert mgr.list_aliases() == []
        assert mgr.resolve("anything") is None
