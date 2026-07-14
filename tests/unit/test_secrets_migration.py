"""Structural tests for secrets/migration.py — secret migration into OpenBao/Vault."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.secrets.migration import (
    migrate_profile_secrets,
    scrub_inline_secrets,
)


class TestMigrateProfileSecrets:
    def test_empty_profiles(self):
        mgr = MagicMock()
        result = migrate_profile_secrets(mgr, [])
        assert result["migrated"] == 0
        assert result["aliases"] == []
        assert result["skipped"] == []

    def test_profile_without_aliases(self):
        mgr = MagicMock()
        result = migrate_profile_secrets(mgr, [
            {"model_profile_id": "p1"},
        ])
        assert result["migrated"] == 0

    @patch("general_ludd.secrets.migration.EnvSecretsManager")
    def test_alias_not_found_in_env(self, mock_env_cls):
        mock_env = MagicMock()
        mock_env.resolve.return_value = None
        mock_env_cls.return_value = mock_env

        mgr = MagicMock()
        result = migrate_profile_secrets(mgr, [
            {"model_profile_id": "p1", "credential_alias": "MISSING_KEY"},
        ])
        assert result["migrated"] == 0
        assert "MISSING_KEY" in result["skipped"]

    @patch("general_ludd.secrets.migration.EnvSecretsManager")
    def test_successful_migration(self, mock_env_cls):
        mock_env = MagicMock()
        mock_env.resolve.return_value = "sk-secret-value"
        mock_env_cls.return_value = mock_env

        mgr = MagicMock()
        result = migrate_profile_secrets(mgr, [
            {"model_profile_id": "p1", "credential_alias": "MY_API_KEY"},
        ])
        assert result["migrated"] == 1
        assert "MY_API_KEY" in result["aliases"]
        mgr.write_secret.assert_called_once()
        mgr.register_alias.assert_called_once()

    @patch("general_ludd.secrets.migration.EnvSecretsManager")
    def test_migration_failure_is_skipped(self, mock_env_cls):
        mock_env = MagicMock()
        mock_env.resolve.return_value = "sk-value"
        mock_env_cls.return_value = mock_env

        mgr = MagicMock()
        mgr.write_secret.side_effect = RuntimeError("vault down")
        result = migrate_profile_secrets(mgr, [
            {"model_profile_id": "p1", "credential_alias": "MY_KEY"},
        ])
        assert result["migrated"] == 0
        assert "MY_KEY" in result["skipped"]

    @patch("general_ludd.secrets.migration.EnvSecretsManager")
    def test_api_base_alias_migrated(self, mock_env_cls):
        mock_env = MagicMock()
        mock_env.resolve.return_value = "https://api.example.com"
        mock_env_cls.return_value = mock_env

        mgr = MagicMock()
        result = migrate_profile_secrets(mgr, [
            {"model_profile_id": "p1", "api_base_alias": "MY_API_BASE"},
        ])
        assert result["migrated"] == 1


class TestScrubInlineSecrets:
    def test_nonexistent_file(self):
        result = scrub_inline_secrets(Path("/nonexistent/file.yml"))
        assert result == []

    def test_scrubs_api_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("name: test\napi_key: abc123\nother: value\n")
            f.flush()
            path = Path(f.name)

        try:
            result = scrub_inline_secrets(path)
            assert "api_key" in result
            content = path.read_text()
            assert "api_key" not in content
            assert "name: test" in content
            assert "other: value" in content
        finally:
            path.unlink(missing_ok=True)

    def test_does_not_scrub_reference_values(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("password: $VAULT_REF\napi_key: null\n")
            f.flush()
            path = Path(f.name)

        try:
            result = scrub_inline_secrets(path)
            # $VAULT_REF, null, None, ~, true, false, '' should not be scrubbed
            assert "password" not in result
        finally:
            path.unlink(missing_ok=True)

    def test_default_secret_fields(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("secret_key: my-secret\naccess_token: tok123\n")
            f.flush()
            path = Path(f.name)

        try:
            result = scrub_inline_secrets(path)
            assert "secret_key" in result
            assert "access_token" in result
        finally:
            path.unlink(missing_ok=True)

    def test_custom_secret_fields(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("custom_secret: val1\nnormal: val2\n")
            f.flush()
            path = Path(f.name)

        try:
            result = scrub_inline_secrets(path, secret_fields=["custom_secret"])
            assert "custom_secret" in result
        finally:
            path.unlink(missing_ok=True)
