"""Unit tests for secrets manager."""

from agentic_harness.secrets.manager import SecretAlias, SecretsManager


class TestSecretsManager:
    def test_list_aliases(self):
        mgr = SecretsManager()
        mgr.register_alias(SecretAlias(alias="db_password", path="db/password"))
        mgr.register_alias(SecretAlias(alias="api_key", path="model/openai/api_key"))
        assert "db_password" in mgr.list_aliases()
        assert "api_key" in mgr.list_aliases()

    def test_resolve_without_client_returns_none(self):
        mgr = SecretsManager()
        mgr.register_alias(SecretAlias(alias="test", path="secret/test"))
        assert mgr.resolve("test") is None

    def test_resolve_unknown_alias_returns_none(self):
        mgr = SecretsManager()
        assert mgr.resolve("nonexistent") is None
