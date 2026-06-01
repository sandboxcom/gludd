"""Tests for secret migration from config/env to OpenBao/Vault.

The migration process:
1. Scan all model profiles for credential_alias and api_base_alias fields
2. Resolve each alias from environment variables
3. Write the resolved value into OpenBao KV v2
4. Register a SecretAlias so future reads come from Vault
5. Verify the value can be read back from Vault
6. If a secret was found inline in a YAML config file, delete it from the file
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import SecretsManager


class FakeKV:
    """In-memory KV v2 store for testing."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def create_or_update_secret(
        self, path: str, secret: dict[str, Any], mount_point: str = "secret"
    ) -> None:
        self._data[f"{mount_point}/{path}"] = secret

    def read_secret_version(
        self, path: str, mount_point: str = "secret"
    ) -> dict[str, Any] | None:
        key = f"{mount_point}/{path}"
        if key not in self._data:
            return None
        return {"data": {"data": self._data[key]}}


def _make_connected_manager() -> tuple[SecretsManager, FakeKV]:
    config = OpenBaoConfig(
        mode="external",
        external_url="https://vault.example.com:8200",
        external_token="test-token",
    )
    mgr = SecretsManager(config=config)
    kv = FakeKV()
    fake_client = MagicMock()
    fake_client.secrets.kv.v2.create_or_update_secret = kv.create_or_update_secret
    fake_client.secrets.kv.v2.read_secret_version = kv.read_secret_version
    fake_client.is_authenticated.return_value = True
    mgr._client = fake_client
    return mgr, kv


class TestSecretMigrationFromEnv:
    """Test that secrets from env vars are migrated into Vault."""

    def test_migrate_credential_alias_from_env(self, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.secrets.migration import migrate_profile_secrets

        monkeypatch.setenv("ZAI_API_KEY", "sk-zai-test-key-12345")
        monkeypatch.setenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")

        mgr, _kv = _make_connected_manager()

        profiles = [
            {
                "model_profile_id": "zai_coder",
                "credential_alias": "ZAI_API_KEY",
                "api_base_alias": "ZAI_BASE_URL",
            },
        ]

        result = migrate_profile_secrets(mgr, profiles)

        assert result["migrated"] == 2
        assert "ZAI_API_KEY" in result["aliases"]
        assert "ZAI_BASE_URL" in result["aliases"]

        assert mgr.resolve("ZAI_API_KEY") == "sk-zai-test-key-12345"
        assert mgr.resolve("ZAI_BASE_URL") == "https://open.bigmodel.cn/api/paas/v4"

    def test_migrate_skips_missing_env_vars(self, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.secrets.migration import migrate_profile_secrets

        monkeypatch.delenv("NONEXISTENT_KEY", raising=False)

        mgr, _kv = _make_connected_manager()

        profiles = [
            {
                "model_profile_id": "test_profile",
                "credential_alias": "NONEXISTENT_KEY",
                "api_base_alias": None,
            },
        ]

        result = migrate_profile_secrets(mgr, profiles)

        assert result["migrated"] == 0
        assert result["skipped"] == ["NONEXISTENT_KEY"]

    def test_migrate_writes_to_vault_kv(self, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.secrets.migration import migrate_profile_secrets

        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

        mgr, kv = _make_connected_manager()

        profiles = [
            {
                "model_profile_id": "openai_gpt4",
                "credential_alias": "OPENAI_API_KEY",
                "api_base_alias": None,
            },
        ]

        migrate_profile_secrets(mgr, profiles)

        stored = kv.read_secret_version("model-profiles/openai_gpt4/credential_alias")
        assert stored is not None
        assert stored["data"]["data"]["value"] == "sk-openai-test"

    def test_migrate_multiple_profiles(self, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.secrets.migration import migrate_profile_secrets

        monkeypatch.setenv("KEY_A", "val-a")
        monkeypatch.setenv("KEY_B", "val-b")

        mgr, _kv = _make_connected_manager()

        profiles = [
            {"model_profile_id": "prof_a", "credential_alias": "KEY_A", "api_base_alias": None},
            {"model_profile_id": "prof_b", "credential_alias": "KEY_B", "api_base_alias": None},
        ]

        result = migrate_profile_secrets(mgr, profiles)
        assert result["migrated"] == 2

        assert mgr.resolve("KEY_A") == "val-a"
        assert mgr.resolve("KEY_B") == "val-b"


class TestSecretScrubbingFromConfig:
    """Test that inline secrets in YAML config files are scrubbed after migration."""

    def test_scrub_secret_from_yaml_file(self, tmp_path):
        from general_ludd.secrets.migration import scrub_inline_secrets

        config_file = tmp_path / "test_profile.yml"
        config_file.write_text(
            "model_profile_id: test\n"
            "api_key: sk-secret-12345\n"
            "credential_alias: TEST_KEY\n"
            "model_name: gpt-4\n"
        )

        scrubbed = scrub_inline_secrets(
            config_file,
            secret_fields=["api_key", "external_token"],
        )

        assert scrubbed == ["api_key"]
        content = config_file.read_text()
        assert "sk-secret-12345" not in content
        assert "api_key:" not in content
        assert "model_profile_id: test" in content
        assert "model_name: gpt-4" in content

    def test_scrub_preserves_file_without_secrets(self, tmp_path):
        from general_ludd.secrets.migration import scrub_inline_secrets

        config_file = tmp_path / "clean.yml"
        config_file.write_text(
            "model_profile_id: test\n"
            "credential_alias: TEST_KEY\n"
            "model_name: gpt-4\n"
        )

        scrubbed = scrub_inline_secrets(
            config_file,
            secret_fields=["api_key", "external_token"],
        )

        assert scrubbed == []
        assert config_file.read_text() == (
            "model_profile_id: test\n"
            "credential_alias: TEST_KEY\n"
            "model_name: gpt-4\n"
        )

    def test_scrub_openbao_token_from_config(self, tmp_path):
        from general_ludd.secrets.migration import scrub_inline_secrets

        config_file = tmp_path / "default.yml"
        config_file.write_text(
            "mode: external\n"
            "external_url: https://vault.example.com:8200\n"
            "external_token: s.root-token-secret\n"
            "kv_mount: secret\n"
        )

        scrubbed = scrub_inline_secrets(
            config_file,
            secret_fields=["external_token", "api_key"],
        )

        assert scrubbed == ["external_token"]
        content = config_file.read_text()
        assert "s.root-token-secret" not in content
        assert "external_url: https://vault.example.com:8200" in content
        assert "mode: external" in content


class TestSecretMigrationIntegration:
    """End-to-end: migrate env vars to Vault, verify read-back, scrub config."""

    def test_full_migration_flow(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.secrets.migration import migrate_profile_secrets, scrub_inline_secrets

        monkeypatch.setenv("PROD_API_KEY", "sk-prod-key-99999")

        mgr, _kv = _make_connected_manager()

        config_file = tmp_path / "prod_profile.yml"
        config_file.write_text(
            "model_profile_id: prod_coder\n"
            "api_key: sk-prod-key-99999\n"
            "credential_alias: PROD_API_KEY\n"
            "model_name: glm-5.1\n"
        )

        profiles = [
            {
                "model_profile_id": "prod_coder",
                "credential_alias": "PROD_API_KEY",
                "api_base_alias": None,
            },
        ]

        result = migrate_profile_secrets(mgr, profiles)
        assert result["migrated"] == 1

        assert mgr.resolve("PROD_API_KEY") == "sk-prod-key-99999"

        scrubbed = scrub_inline_secrets(config_file, secret_fields=["api_key"])
        assert scrubbed == ["api_key"]

        content = config_file.read_text()
        assert "sk-prod-key-99999" not in content
        assert "credential_alias: PROD_API_KEY" in content

        assert mgr.resolve("PROD_API_KEY") == "sk-prod-key-99999"
