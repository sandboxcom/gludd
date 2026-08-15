"""Tests for ZAI secrets resolution — uppercase env var fallback (Bug 1 + Bug 2)."""
from __future__ import annotations

import os

from general_ludd.secrets import EnvSecretsManager

# Lowercase env var alias used to verify case-insensitive resolution (SIM112
# fires on string literals that look like env vars; binding to a variable
# avoids the lint without changing test semantics).
_ZAI_API_KEY_ALIAS = "zai_api_key"


class TestZaiSecretsResolution:
    def teardown_method(self, method):
        """Clean up env vars after each test."""
        for key in ("ZAI_API_KEY", "zai_api_key", "ZAI_BASE_URL", "zai_api_base", "ZAI_API_BASE"):
            os.environ.pop(key, None)

    def test_zai_api_key_uppercase_env_resolves(self, monkeypatch):
        """Bug 1: ZAI_API_KEY (uppercase) resolves via lowercase alias zai_api_key."""
        monkeypatch.setenv("ZAI_API_KEY", "test-key-uppercase")
        mgr = EnvSecretsManager()
        assert mgr.resolve("zai_api_key") == "test-key-uppercase"

    def test_zai_base_url_env_resolves_as_api_base(self, monkeypatch):
        """Bug 2: ZAI_BASE_URL resolves via alias zai_api_base."""
        monkeypatch.setenv("ZAI_BASE_URL", "https://test.example.com/v4")
        mgr = EnvSecretsManager()
        assert mgr.resolve("zai_api_base") == "https://test.example.com/v4"

    def test_lowercase_alias_direct_env_still_works(self, monkeypatch):
        """Regression: lowercase env var still resolves (existing path not broken)."""
        monkeypatch.setenv(_ZAI_API_KEY_ALIAS, "direct-key-lowercase")
        mgr = EnvSecretsManager()
        assert mgr.resolve(_ZAI_API_KEY_ALIAS) == "direct-key-lowercase"

    def test_override_takes_precedence_over_env(self, monkeypatch):
        """Explicit set() always wins over ambient env."""
        monkeypatch.setenv("ZAI_API_KEY", "env-key")
        mgr = EnvSecretsManager()
        mgr.set("zai_api_key", "override-key")
        assert mgr.resolve("zai_api_key") == "override-key"

    def test_non_allowlisted_name_still_blocked(self, monkeypatch):
        """Security regression: non-allowlisted names are still blocked."""
        monkeypatch.setenv("GLUDD_AUTH_PSK", "should-not-resolve")
        mgr = EnvSecretsManager()
        assert mgr.resolve("GLUDD_AUTH_PSK") is None
        assert mgr.resolve("gludd_psk") is None
