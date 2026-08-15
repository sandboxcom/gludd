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
        # S-1: a non-credential ambient env var (e.g. GLUDD_AUTH_PSK, PATH) must NOT
        # be resolvable from os.environ — fail-closed.
        monkeypatch.setenv("_TEST_PLAIN_VAR", "envval")
        mgr = EnvSecretsManager()
        assert mgr.resolve("_TEST_PLAIN_VAR") is None

    def test_gludd_psk_is_never_resolved_from_env(self, monkeypatch):
        # S-1: the PSK must never leak through the secrets manager.
        monkeypatch.setenv("GLUDD_AUTH_PSK", "super-secret-psk")
        mgr = EnvSecretsManager()
        assert mgr.resolve("GLUDD_AUTH_PSK") is None

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

    # ── allow_env ────────────────────────────────────────────────────────

    def test_allow_env_adds_names_to_allow_set(self, monkeypatch):
        monkeypatch.setenv("_TEST_VOUCHED_1", "v1")
        monkeypatch.setenv("_TEST_VOUCHED_2", "v2")
        mgr = EnvSecretsManager()
        mgr.allow_env("_TEST_VOUCHED_1", "_TEST_VOUCHED_2")
        assert mgr.resolve("_TEST_VOUCHED_1") == "v1"
        assert mgr.resolve("_TEST_VOUCHED_2") == "v2"

    def test_allow_env_then_non_allowlisted_still_blocked(self, monkeypatch):
        monkeypatch.setenv("_TEST_BLOCKED", "secret")
        mgr = EnvSecretsManager()
        mgr.allow_env("_TEST_VOUCHED")
        assert mgr.resolve("_TEST_BLOCKED") is None

    # ── uppercase fallback ──────────────────────────────────────────────

    def test_uppercase_fallback_allowlisted(self, monkeypatch):
        monkeypatch.setenv("TEST_THING_API_KEY", "uppercase-val")
        mgr = EnvSecretsManager()
        assert mgr.resolve("test_thing_api_key") == "uppercase-val"

    def test_uppercase_fallback_not_allowlisted_still_blocked(self, monkeypatch):
        monkeypatch.setenv("PLAIN_VAR", "secret")
        mgr = EnvSecretsManager()
        assert mgr.resolve("plain_var") is None

    # ── explicit alias mapping (zai_api_base → ZAI_BASE_URL) ────────────

    def test_explicit_alias_mapping_zai_api_base(self, monkeypatch):
        monkeypatch.setenv("ZAI_BASE_URL", "https://api.example.com/v1")
        mgr = EnvSecretsManager()
        assert mgr.resolve("zai_api_base") == "https://api.example.com/v1"

    def test_explicit_alias_mapping_when_env_var_missing(self, monkeypatch):
        mgr = EnvSecretsManager()
        assert mgr.resolve("zai_api_base") is None

    # ── all allowlist patterns ──────────────────────────────────────────

    def test_allowlist_pattern_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        assert EnvSecretsManager().resolve("OPENAI_API_KEY") == "sk-fake"

    def test_allowlist_pattern_api_base(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_BASE", "https://base.example.com")
        assert EnvSecretsManager().resolve("OPENAI_API_BASE") == "https://base.example.com"

    def test_allowlist_pattern_base_url(self, monkeypatch):
        monkeypatch.setenv("MODEL_BASE_URL", "https://models.example.com")
        assert EnvSecretsManager().resolve("MODEL_BASE_URL") == "https://models.example.com"

    def test_allowlist_pattern_api_url(self, monkeypatch):
        monkeypatch.setenv("SLURM_API_URL", "https://slurm.example.com")
        assert EnvSecretsManager().resolve("slurm_api_url") == "https://slurm.example.com"

    def test_allowlist_pattern_auth_token(self, monkeypatch):
        monkeypatch.setenv("SLURM_AUTH_TOKEN", "tok123")
        assert EnvSecretsManager().resolve("slurm_auth_token") == "tok123"

    def test_allowlist_pattern_gludd_secret_prefix(self, monkeypatch):
        monkeypatch.setenv("GLUDD_SECRET_DB_PASS", "dbpass123")
        assert EnvSecretsManager().resolve("GLUDD_SECRET_DB_PASS") == "dbpass123"

    # ── project_id passthrough ──────────────────────────────────────────

    def test_project_id_is_accepted_but_ignored_for_env(self, monkeypatch):
        monkeypatch.setenv("PROJ_API_KEY", "projval")
        mgr = EnvSecretsManager()
        assert mgr.resolve("PROJ_API_KEY", project_id="proj-001") == "projval"

    # ── set overwrite ───────────────────────────────────────────────────

    def test_set_overwrites_previous_override(self):
        mgr = EnvSecretsManager(overrides={"KEY": "old"})
        mgr.set("KEY", "new")
        assert mgr.resolve("KEY") == "new"

    def test_set_then_resolve_overrides_environ(self, monkeypatch):
        monkeypatch.setenv("KEY_API_KEY", "envval")
        mgr = EnvSecretsManager()
        mgr.set("KEY_API_KEY", "override")
        assert mgr.resolve("KEY_API_KEY") == "override"

    # ── edge: empty values ──────────────────────────────────────────────

    def test_empty_string_env_value_is_returned(self, monkeypatch):
        monkeypatch.setenv("EMPTY_API_KEY", "")
        assert EnvSecretsManager().resolve("EMPTY_API_KEY") == ""

    def test_empty_string_override_is_returned(self):
        mgr = EnvSecretsManager(overrides={"EMPTY_API_KEY": ""})
        assert mgr.resolve("EMPTY_API_KEY") == ""

    # ── edge: list_aliases excludes env-only entries ────────────────────

    def test_list_aliases_only_includes_overrides_not_env(self, monkeypatch):
        monkeypatch.setenv("ENV_API_KEY", "envval")
        mgr = EnvSecretsManager(overrides={"b": "2"})
        assert mgr.list_aliases() == ["b"]

    def test_list_aliases_after_set(self):
        mgr = EnvSecretsManager()
        mgr.set("a", "1")
        mgr.set("b", "2")
        assert mgr.list_aliases() == ["a", "b"]
