"""Unit tests for secrets manager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    AppRoleCreds,
    BootstrapResult,
    ImageUpdateCandidate,
    SecretAlias,
    SecretsManager,
)


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


class TestOpenBaoConfig:
    def test_openbao_config_defaults(self):
        cfg = OpenBaoConfig()
        assert cfg.mode == "auto"
        assert cfg.external_url is None
        assert cfg.external_token is None
        assert cfg.local_image == "ghcr.io/openbao/openbao"
        assert cfg.local_image_digest_pin is None
        assert cfg.local_container_runtime == "podman_preferred"
        assert cfg.kv_mount == "secret"
        assert cfg.auth_method == "approle"
        assert cfg.approle_role_name == "agentic-harness"
        assert cfg.weekly_image_update_scan is True
        assert cfg.weekly_image_update_creates_manual_hold is True

    def test_openbao_external_config_wins(self):
        cfg = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.xtccwshhwechat-token",
            kv_mount="custom-kv",
        )
        assert cfg.mode == "external"
        assert cfg.external_url == "https://bao.example.com:8200"
        assert cfg.external_token == "s.xtccwshhwechat-token"
        assert cfg.kv_mount == "custom-kv"


class TestOpenBaoSecretsManager:
    def _make_manager(self, **kwargs: object) -> SecretsManager:
        cfg = OpenBaoConfig(**kwargs)
        return SecretsManager(config=cfg)

    def test_openbao_bootstrap_local(self):
        mgr = self._make_manager(mode="auto")
        result = mgr.bootstrap_local()
        assert isinstance(result, BootstrapResult)
        assert result.initialized is True
        assert result.url is not None
        assert result.token is not None

    def test_openbao_connect_external(self):
        mock_client = MagicMock()
        with patch("general_ludd.secrets.manager.hvac") as mock_hvac:
            mock_hvac.Client.return_value = mock_client
            mgr = self._make_manager(
                mode="external",
                external_url="https://bao.example.com:8200",
                external_token="s.ext-token",
            )
            mgr.connect()
            mock_hvac.Client.assert_called_once()
            assert mgr._client is mock_client

    def test_openbao_setup_approle(self):
        mgr = self._make_manager()
        mock_client = MagicMock()
        mgr._client = mock_client

        mock_client.auth.approle.create_role.return_value = {}
        mock_client.auth.approle.read_role_id.return_value = {
            "data": {"role_id": "role-abc-123"}
        }
        with patch.object(
            mgr, "_generate_secret_id", return_value="secret-xyz-789"
        ):
            creds = mgr.setup_approle("test-role")

        assert isinstance(creds, AppRoleCreds)
        assert creds.role_id == "role-abc-123"
        assert creds.secret_id == "secret-xyz-789"

    def test_openbao_write_read_secret(self):
        mgr = self._make_manager()
        mock_client = MagicMock()
        mgr._client = mock_client

        test_data = {"username": "admin", "password": "hunter2"}
        mgr.write_secret("myapp/config", test_data)
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once()

        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": test_data}
        }
        result = mgr.read_secret("myapp/config")
        assert result == test_data

    def test_openbao_read_secret_missing(self):
        mgr = self._make_manager()
        mock_client = MagicMock()
        mgr._client = mock_client

        mock_client.secrets.kv.v2.read_secret_version.return_value = None
        result = mgr.read_secret("nonexistent/path")
        assert result is None

    def test_openbao_pin_image_digest(self):
        mgr = self._make_manager()
        mock_client = MagicMock()
        mgr._client = mock_client

        mgr.pin_image_digest(
            "ghcr.io/openbao/openbao",
            "sha256:abcdef123456",
        )
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once()
        call_args = mock_client.secrets.kv.v2.create_or_update_secret.call_args
        secret_data = call_args[1]["secret"]
        assert secret_data["image_ref"] == "ghcr.io/openbao/openbao"
        assert secret_data["pinned_digest"] == "sha256:abcdef123456"

    def test_openbao_scan_for_image_updates(self):
        mgr = self._make_manager()
        mock_client = MagicMock()
        mgr._client = mock_client

        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {
                "data": {
                    "image_ref": "ghcr.io/openbao/openbao",
                    "pinned_digest": "sha256:aaaa1111",
                }
            }
        }
        with patch.object(
            mgr,
            "_fetch_remote_digest",
            return_value="sha256:bbbb2222",
        ):
            candidate = mgr.scan_for_image_updates()

        assert isinstance(candidate, ImageUpdateCandidate)
        assert candidate.current_digest == "sha256:aaaa1111"
        assert candidate.candidate_digest == "sha256:bbbb2222"
        assert candidate.registry == "ghcr.io"

    def test_openbao_scan_no_update(self):
        mgr = self._make_manager()
        mock_client = MagicMock()
        mgr._client = mock_client

        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {
                "data": {
                    "image_ref": "ghcr.io/openbao/openbao",
                    "pinned_digest": "sha256:aaaa1111",
                }
            }
        }
        with patch.object(
            mgr,
            "_fetch_remote_digest",
            return_value="sha256:aaaa1111",
        ):
            candidate = mgr.scan_for_image_updates()

        assert candidate is None

    def test_openbao_secrets_not_logged(self, capfd):
        mgr = self._make_manager(
            external_url="https://bao.example.com",
            external_token="s.super-secret-token",
        )
        mgr.bootstrap_local()
        captured = capfd.readouterr()
        assert "s.super-secret-token" not in captured.out
        assert "s.super-secret-token" not in captured.err


class TestOpenBaoPlaybooks:
    def test_openbao_bootstrap_playbook_exists(self):
        import os

        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "playbooks", "openbao_bootstrap.yml"
        )
        assert os.path.exists(path), f"Playbook not found: {path}"

    def test_openbao_update_scan_playbook_exists(self):
        import os

        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "playbooks",
            "openbao_image_update_scan.yml",
        )
        assert os.path.exists(path), f"Playbook not found: {path}"
