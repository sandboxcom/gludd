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


class TestOpenBaoConfigSecretLeakPrevention:
    """Regression tests: external_token must not appear in repr or serialized output."""

    _TOKEN = "s.super-secret-should-not-leak"

    def _make_cfg(self) -> OpenBaoConfig:
        return OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token=self._TOKEN,
        )

    def test_repr_does_not_contain_token(self):
        cfg = self._make_cfg()
        assert self._TOKEN not in repr(cfg), (
            f"external_token leaked into repr: {cfg!r}"
        )

    def test_model_dump_does_not_contain_token(self):
        cfg = self._make_cfg()
        dumped = cfg.model_dump()
        assert dumped["external_token"] != self._TOKEN, (
            "external_token leaked into model_dump() output"
        )

    def test_model_dump_json_does_not_contain_token(self):
        cfg = self._make_cfg()
        json_str = cfg.model_dump_json()
        assert self._TOKEN not in json_str, (
            f"external_token leaked into model_dump_json() output: {json_str}"
        )

    def test_direct_attribute_access_still_returns_raw_value(self):
        """Direct attribute access must still return the real token (for consumers like manager.py)."""
        cfg = self._make_cfg()
        assert cfg.external_token == self._TOKEN

    def test_none_token_serializes_to_none(self):
        cfg = OpenBaoConfig()
        dumped = cfg.model_dump()
        assert dumped["external_token"] is None


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
        mgr = self._make_manager(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.ext-token",
        )
        with patch("general_ludd.secrets.manager.hvac.Client", return_value=mock_client) as mock_client_cls:
            mgr.connect()
        mock_client_cls.assert_called_once()
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


class TestSecretsRedactionGap:
    """secrets/manager.py:_redact / _sanitize_error — widened redaction.

    Regression coverage for the finding that the old ``_redact`` only masked
    a run of 20+ contiguous base64-charset chars, so shorter or non-base64
    secrets embedded in exception text reached ``logger.error`` unredacted.
    """

    def _make_manager(self, **kwargs: object) -> SecretsManager:
        cfg = OpenBaoConfig(**kwargs)
        return SecretsManager(config=cfg)

    def test_normal_message_unaffected(self):
        """Ordinary prose must survive redaction unmangled."""
        mgr = self._make_manager()
        exc = RuntimeError("connection refused: timeout after 30s")
        sanitized = mgr._sanitize_error(exc)
        assert "connection refused" in sanitized
        assert "timeout after 30s" in sanitized
        assert "REDACTED" not in sanitized

    def test_prose_mentioning_secret_words_without_values_unaffected(self):
        """Bare mentions of 'password'/'secret'/'token' with no adjacent value
        must not be redacted into mush — only key=value-shaped text is."""
        mgr = self._make_manager()
        exc = RuntimeError(
            "secret configuration mismatch: the password prompt failed and "
            "the token endpoint returned 500"
        )
        sanitized = mgr._sanitize_error(exc)
        assert "secret configuration mismatch" in sanitized
        assert "password prompt failed" in sanitized
        assert "token endpoint returned 500" in sanitized
        assert "REDACTED" not in sanitized

    def test_long_base64_blob_still_redacted(self):
        """Regression: the original 20+-char context-free heuristic must still work."""
        mgr = self._make_manager()
        blob = "aB3dEfGh1JkLmN0pQrStUvWxYz9876543210"  # 37 chars, no context word  # pragma: allowlist secret
        exc = RuntimeError(f"unexpected backend response: {blob}")
        sanitized = mgr._sanitize_error(exc)
        assert blob not in sanitized
        assert "REDACTED" in sanitized

    def test_short_contextual_secret_redacted(self):
        """A shorter (12+ char) blob directly after a key-ish word + separator
        is redacted even though it's below the old 20-char threshold."""
        mgr = self._make_manager()
        exc = RuntimeError("auth failed with token=abcdef123456xyz")
        sanitized = mgr._sanitize_error(exc)
        assert "abcdef123456xyz" not in sanitized
        assert "REDACTED" in sanitized

    def test_short_known_secret_value_redacted_when_it_matches_stored_secret(self):
        """The manager exact-match redacts any secret VALUE it has itself
        observed, regardless of length/shape — catching a short, non-base64
        secret like 'abc12345key' that no regex heuristic would flag."""
        mgr = self._make_manager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"api_key": "abc12345key"}}  # pragma: allowlist secret
        }
        stored = mgr.read_secret("myapp/config")
        assert stored == {"api_key": "abc12345key"}  # pragma: allowlist secret

        exc = RuntimeError("upstream rejected credential abc12345key")
        sanitized = mgr._sanitize_error(exc)
        assert "abc12345key" not in sanitized
        assert "REDACTED" in sanitized

    def test_short_unrelated_value_not_redacted(self):
        """A short value the manager has NEVER seen, and that isn't
        key=value-shaped, is left alone (no over-redaction)."""
        mgr = self._make_manager()
        exc = RuntimeError("request id abc12345key was not found")
        sanitized = mgr._sanitize_error(exc)
        assert "abc12345key" in sanitized
        assert "REDACTED" not in sanitized

    def test_container_token_redacted_via_known_secret_tracking(self):
        """H-1 dev container token, once minted, is redacted from later errors."""
        mgr = self._make_manager()
        mgr._container_token = "deadbeef12345678"  # pragma: allowlist secret
        mgr._track_secret_value(mgr._container_token)
        exc = RuntimeError("dev container auth failed: deadbeef12345678")  # pragma: allowlist secret
        sanitized = mgr._sanitize_error(exc)
        assert "deadbeef12345678" not in sanitized
        assert "REDACTED" in sanitized


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
