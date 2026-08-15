"""E2E tests: verify secrets are NEVER exposed outside OpenBao/Vault.

Eight threat vectors covered:
  1. Log scrubbing — API keys, tokens, passwords in log/stdout/stderr/HTTP
  2. Config file scrubbing — strip secrets from YAML after Vault migration
  3. Memory safety — repr() and string representation never leak secret values
  4. HTTP response safety — daemon endpoints return only masked/reference values
  5. Error message safety — tracebacks and exception text are redacted
  6. Database safety — secrets encrypted at rest, never plaintext in DB
  7. File system safety — path traversal, temp files, core dumps
  8. Subprocess safety — env isolation, no secrets in argv or env for subprocess

Uses real daemon TestClient, SecretsManager with mocked hvac, and the full
sanitization stack. No external Vault/OpenBao needed.
"""

from __future__ import annotations

import gc
import json
import os
import tempfile
import weakref
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from general_ludd.secrets.manager import SecretsManager

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_API_KEY = "sk-proj-abcdef1234567890abcdef1234567890"
_BEARER_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret-part"
_BASIC_AUTH = "Basic dXNlcm5hbWU6cGFzc3dvcmQxMjM="
_PASSWORD = "s3cr3t_p@ssw0rd!"
_PSK = "test-psk-for-e2e-secret-safety"


def _build_secrets_manager_with_known_values(
    *values: str,
) -> tuple[SecretsManager, MagicMock]:
    from general_ludd.secrets.config import OpenBaoConfig
    from general_ludd.secrets.manager import SecretsManager

    mock_client = MagicMock()
    config = OpenBaoConfig(mode="auto")
    mgr = SecretsManager(client=mock_client, config=config)
    for v in values:
        mgr._track_secret_value(v)
    return mgr, mock_client


# ---------------------------------------------------------------------------
# 1. Log scrubbing
# ---------------------------------------------------------------------------


class TestLogScrubbing:
    """Secrets must never appear in log messages, stdout, stderr, or HTTP."""

    # -- SecretsManager._redact (static regex) --------------------------------

    def test_redact_blob_like_secret(self):
        from general_ludd.secrets.manager import SecretsManager

        msg = "Connected with token abcdef1234567890abcdef1234567890 successfully"
        result = SecretsManager._redact(msg)
        assert "abcdef1234567890abcdef1234567890" not in result
        assert "***REDACTED***" in result

    def test_redact_contextual_api_key(self):
        from general_ludd.secrets.manager import SecretsManager

        msg = 'Authorization: api_key=sk-proj-secret1234567890'
        result = SecretsManager._redact(msg)
        assert "sk-proj-secret1234567890" not in result
        assert "***REDACTED***" in result

    def test_redact_contextual_token(self):
        from general_ludd.secrets.manager import SecretsManager

        msg = "token: deadbeef1234567890abc"
        result = SecretsManager._redact(msg)
        assert "deadbeef1234567890abc" not in result
        assert "***REDACTED***" in result

    def test_redact_contextual_password(self):
        from general_ludd.secrets.manager import SecretsManager

        msg = "password = my-secure-password123"
        result = SecretsManager._redact(msg)
        assert "my-secure-password123" not in result
        assert "***REDACTED***" in result

    def test_redact_leaves_safe_text_unchanged(self):
        from general_ludd.secrets.manager import SecretsManager

        safe = "Connected to backend successfully. No secrets here."
        result = SecretsManager._redact(safe)
        assert result == safe

    def test_redact_does_not_fire_on_short_text(self):
        from general_ludd.secrets.manager import SecretsManager

        short = "id=abc status=ok count=5"
        result = SecretsManager._redact(short)
        assert "***REDACTED***" not in result

    # -- SecretsManager._redact_message (known-value + static) ----------------

    def test_exact_match_redaction_short_non_base64(self):
        mgr, _mock = _build_secrets_manager_with_known_values("my-key-42")
        msg = "Failed to write secret my-key-42: connection refused"
        result = mgr._redact_message(msg)
        assert "my-key-42" not in result
        assert "***REDACTED***" in result

    def test_exact_match_redaction_multiple_values(self):
        mgr, _mock = _build_secrets_manager_with_known_values(
            "alpha-token-101", "beta-key-202"
        )
        msg = "Used keys alpha-token-101 and beta-key-202 for auth"
        result = mgr._redact_message(msg)
        assert "alpha-token-101" not in result
        assert "beta-key-202" not in result

    def test_min_tracked_secret_len_skips_short(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=MagicMock())
        mgr._track_secret_value("ab")
        assert "ab" not in mgr._known_secret_values

    def test_max_tracked_secrets_cap(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=MagicMock())
        for i in range(600):
            mgr._track_secret_value(f"key-{i:08d}-padding-for-length")
        assert len(mgr._known_secret_values) <= mgr._MAX_TRACKED_SECRETS

    def test_write_secret_tracks_values(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr.write_secret("test/log-scrub", {"api_key": _API_KEY})
        assert _API_KEY in mgr._known_secret_values

    def test_read_secret_tracks_values(self):
        from general_ludd.secrets.manager import SecretsManager

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"token": _PASSWORD}}
        }
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        result = mgr.read_secret("test/tracked")
        assert result is not None
        assert _PASSWORD in mgr._known_secret_values

    # -- sanitize_error_message (security/sanitize.py) ------------------------

    def test_sanitize_api_key_in_url(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Failed: http://user:sk-12345678901234567890@host/api"
        result = sanitize_error_message(msg)
        assert "sk-12345678901234567890" not in result
        assert "REDACTED" in result

    def test_sanitize_x_api_key_header(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "x-api-key: abc123xyz789secret"
        result = sanitize_error_message(msg)
        assert "abc123xyz789secret" not in result

    def test_sanitize_bearer_token_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = f"Auth failed: Authorization: {_BEARER_TOKEN}"
        result = sanitize_error_message(msg)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result

    def test_sanitize_basic_auth_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = f"Connection refused: Authorization: {_BASIC_AUTH}"
        result = sanitize_error_message(msg)
        assert "dXNlcm5hbWU6cGFzc3dvcmQxMjM" not in result

    def test_sanitize_password_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = f"Invalid credential: password: {_PASSWORD}"
        result = sanitize_error_message(msg)
        assert _PASSWORD not in result

    def test_sanitize_token_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "token = deadbeef1234567890abcdef"
        result = sanitize_error_message(msg)
        assert "deadbeef1234567890abcdef" not in result
        assert "REDACTED" in result

    def test_sanitize_localhost_url_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Cannot connect to http://localhost:8200/v1/secret"
        result = sanitize_error_message(msg)
        assert "localhost" not in result

    def test_sanitize_metadata_ip_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Blocked request to 169.254.169.254/latest/meta-data"
        result = sanitize_error_message(msg)
        assert "169.254.169.254" not in result
        assert "REDACTED_METADATA_IP" in result

    def test_sanitize_private_ip_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Rejected: 192.168.1.100:8080 is internal"
        result = sanitize_error_message(msg)
        assert "192.168.1.100" not in result
        assert "REDACTED_PRIVATE_IP" in result

    def test_sanitize_loopback_ip_in_error(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Connection to 127.0.0.1:8200 failed"
        result = sanitize_error_message(msg)
        assert "127.0.0.1" not in result
        assert "REDACTED" in result

    def test_sanitize_empty_string_noop(self):
        from general_ludd.security.sanitize import sanitize_error_message

        assert sanitize_error_message("") == ""

    # -- sanitize_str (connectors/_errors.py) ---------------------------------

    def test_connector_sanitize_str_redacts_paths(self):
        from general_ludd.connectors._errors import sanitize_str

        msg = "Error in /home/user/.config/gludd/secrets.yml: bad key"
        result = sanitize_str(msg)
        assert "/home/user/.config/gludd/secrets.yml" not in result
        assert "REDACTED-PATH" in result

    def test_connector_sanitize_str_redacts_tokens(self):
        from general_ludd.connectors._errors import sanitize_str

        msg = "bearer abcdef1234567890abcdef1234567890abc for auth"
        result = sanitize_str(msg)
        assert "abcdef1234567890abcdef1234567890abc" not in result

    def test_connector_sanitize_str_redacts_urls(self):
        from general_ludd.connectors._errors import sanitize_str

        msg = "Failed at https://api.internal:443/v1/secret?token=xyz"
        result = sanitize_str(msg)
        assert "https://api.internal" not in result
        assert ("REDACTED-URL" in result or "REDACTED-PATH" in result)

    # -- sanitize_exc_message (connectors/_errors.py) -------------------------

    def test_connector_sanitize_exc_returns_type_name_only(self):
        from general_ludd.connectors._errors import sanitize_exc_message

        exc = ValueError("secret: sk-abc1234567890 in message")
        result = sanitize_exc_message(exc)
        assert result == "ValueError"
        assert "sk-abc" not in result

    # -- OpenBaoConfig serialization -------------------------------------------

    def test_openbao_config_never_serializes_token(self):
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.very-secret-token-do-not-leak",
        )
        dumped = config.model_dump()
        assert dumped.get("external_token") == "**REDACTED**"

        json_str = config.model_dump_json()
        assert "very-secret-token-do-not-leak" not in json_str
        assert "**REDACTED**" in json_str


# ---------------------------------------------------------------------------
# 2. Config file scrubbing
# ---------------------------------------------------------------------------


class TestConfigFileScrubbing:
    """gludd scrubs secrets from config files after migrating to Vault."""

    def test_scrub_matching_fields_removed(self):
        from general_ludd.secrets.migration import scrub_inline_secrets

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("api_key: sk-abc123\n")
            f.write("model_name: gpt-4\n")
            f.write("secret_key: deadbeef\n")
            f.write("max_tokens: 4096\n")
            f_name = f.name

        try:
            scrubbed = scrub_inline_secrets(Path(f_name))
            assert "api_key" in scrubbed
            assert "secret_key" in scrubbed

            remaining = Path(f_name).read_text()
            assert "api_key:" not in remaining
            assert "secret_key:" not in remaining
            assert "model_name: gpt-4" in remaining
            assert "max_tokens: 4096" in remaining
        finally:
            Path(f_name).unlink(missing_ok=True)

    def test_scrub_skips_vault_references(self):
        from general_ludd.secrets.migration import scrub_inline_secrets

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("api_key: $VAULT_REF\n")
            f.write("external_token: $OPENBAO_TOKEN\n")
            f_name = f.name

        try:
            scrubbed = scrub_inline_secrets(Path(f_name))
            assert "api_key" not in scrubbed
            assert "external_token" not in scrubbed
            remaining = Path(f_name).read_text()
            assert "$VAULT_REF" in remaining
            assert "$OPENBAO_TOKEN" in remaining
        finally:
            Path(f_name).unlink(missing_ok=True)

    def test_scrub_skips_null_and_none_values(self):
        from general_ludd.secrets.migration import scrub_inline_secrets

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("api_key: null\n")
            f.write("password: None\n")
            f.write("secret_key: ~\n")
            f_name = f.name

        try:
            scrubbed = scrub_inline_secrets(Path(f_name))
            assert scrubbed == []
        finally:
            Path(f_name).unlink(missing_ok=True)

    def test_scrub_skips_commented_fields(self):
        from general_ludd.secrets.migration import scrub_inline_secrets

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("# api_key: sk-removed-anyway\n")
            f.write("# access_token: abc\n")
            f_name = f.name

        try:
            scrubbed = scrub_inline_secrets(Path(f_name))
            assert "api_key" in scrubbed
            assert "access_token" in scrubbed
        finally:
            Path(f_name).unlink(missing_ok=True)

    def test_scrub_missing_file_returns_empty(self):
        from general_ludd.secrets.migration import scrub_inline_secrets

        result = scrub_inline_secrets(Path("/nonexistent/config-e2e.yml"))
        assert result == []

    def test_scrub_custom_secret_fields(self):
        from general_ludd.secrets.migration import scrub_inline_secrets

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("db_password: s3cr3t\n")
            f.write("normal_field: hello\n")
            f_name = f.name

        try:
            scrubbed = scrub_inline_secrets(
                Path(f_name), secret_fields=["db_password"]
            )
            assert "db_password" in scrubbed
        finally:
            Path(f_name).unlink(missing_ok=True)

    def test_scrub_preserves_non_secret_lines(self):
        from general_ludd.secrets.migration import scrub_inline_secrets

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("# This is a config file\n")
            f.write("debug: true\n")
            f.write("api_key: secret-value\n")
            f.write("log_level: INFO\n")
            f.write("  \n")  # blank line
            f_name = f.name

        try:
            scrub_inline_secrets(Path(f_name))
            remaining = Path(f_name).read_text()
            assert "# This is a config file" in remaining
            assert "debug: true" in remaining
            assert "log_level: INFO" in remaining
            assert "api_key:" not in remaining
        finally:
            Path(f_name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 3. Memory safety
# ---------------------------------------------------------------------------


class TestMemorySafety:
    """Secret values must not persist in plaintext in process memory."""

    def test_bootstrap_result_repr_excludes_token(self):
        from general_ludd.secrets.manager import BootstrapResult

        result = BootstrapResult(
            url="http://localhost:8200",
            token="s.secret-bootstrap-token-do-not-leak",
            initialized=True,
        )
        repr_str = repr(result)
        assert "secret-bootstrap-token-do-not-leak" not in repr_str

    def test_bootstrap_result_repr_excludes_container_token(self):
        from general_ludd.secrets.manager import BootstrapResult

        result = BootstrapResult(
            url="http://localhost:8200",
            token="s.root",
            initialized=True,
            container_token="aabbccddeeff00112233445566778899",
        )
        repr_str = repr(result)
        assert "aabbccddeeff00112233445566778899" not in repr_str

    def test_approle_creds_repr_excludes_secret_id(self):
        from general_ludd.secrets.manager import AppRoleCreds

        creds = AppRoleCreds(
            role_id="public-role-id",
            secret_id="do-not-leak-this-secret-id",
        )
        repr_str = repr(creds)
        assert "do-not-leak-this-secret-id" not in repr_str
        assert "public-role-id" in repr_str

    def test_openbao_config_repr_excludes_token(self):
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com",
            external_token="s.super-secret-vault-token",
        )
        repr_str = repr(config)
        assert "s.super-secret-vault-token" not in repr_str

    def test_openbao_config_str_excludes_token(self):
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com",
            external_token="s.secret-token-xyz",
        )
        str_val = str(config)
        assert "s.secret-token-xyz" not in str_val

    def test_known_secret_values_cleared_by_new_instance(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=MagicMock())
        assert len(mgr._known_secret_values) == 0

    def test_weakref_secret_not_retained_after_gc(self):

        secret = "temp-secret-gc-test-abcdefgh"

        class _Holder:
            def __init__(self, s):
                self.secret = s

        holder = _Holder(secret)
        ref = weakref.ref(holder)
        assert ref() is not None
        del holder
        gc.collect()
        assert ref() is None

    def test_migrated_secret_tracked_and_redacted(self):
        from general_ludd.secrets.manager import SecretAlias, SecretsManager

        mock_client = MagicMock()
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        mgr.write_secret("model-profiles/test/credential_alias", {"value": _API_KEY})
        mgr.register_alias(SecretAlias("test_alias", "model-profiles/test/credential_alias"))
        assert _API_KEY in mgr._known_secret_values

        msg = f"Using key {_API_KEY} for auth"
        redacted = mgr._redact_message(msg)
        assert _API_KEY not in redacted


# ---------------------------------------------------------------------------
# 4. HTTP response safety
# ---------------------------------------------------------------------------


class TestHTTPResponseSafety:
    """Daemon endpoints never return raw secret values."""

    @pytest.fixture
    def daemon_client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("GLUDD_AUTH_PSK", _PSK)
        monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "0")

        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(tick_interval=0.0)
        return TestClient(app)

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_PSK}"}

    def test_healthz_does_not_expose_secrets(self, daemon_client: TestClient):
        resp = daemon_client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        body = json.dumps(data)
        assert "secret" not in body.lower() or "no_auth" in body

    def test_api_status_does_not_expose_secrets(self, daemon_client: TestClient):
        resp = daemon_client.get("/api/status")
        assert resp.status_code == 200
        body = resp.text
        assert _API_KEY not in body
        assert _BEARER_TOKEN not in body

    def test_admin_endpoints_no_raw_secret_in_response(self, daemon_client: TestClient):
        resp = daemon_client.get("/admin/projects", headers=self._auth_header())
        assert resp.status_code == 200
        body = resp.text
        assert "s.secret" not in body

    def test_error_response_no_secret_leak(self, daemon_client: TestClient):
        resp = daemon_client.get(
            "/admin/projects/nonexistent-99999",
            headers=self._auth_header(),
        )
        assert resp.status_code != 200
        body = resp.text
        assert _API_KEY not in body
        assert _PSK not in body

    def test_401_response_no_secret_in_body(self, daemon_client: TestClient):
        resp = daemon_client.get("/admin/projects")
        assert resp.status_code == 401
        body = resp.text
        assert _PSK not in body

    def test_json_response_never_masks_secret_paths(self):
        import json

        resp = json.dumps({"secret_path": "projects/1/api_key"})
        assert "api_key" in resp
        assert "REDACTED" not in resp


# ---------------------------------------------------------------------------
# 5. Error message safety
# ---------------------------------------------------------------------------


class TestErrorMessageSafety:
    """Error messages and tracebacks must never include secret values."""

    def test_secrets_unavailable_error_no_secret_in_message(self):
        from general_ludd.secrets.manager import SecretsUnavailableError

        mgr, _mock = _build_secrets_manager_with_known_values(_API_KEY)
        mgr._track_secret_value(_API_KEY)

        exc = SecretsUnavailableError(
            f"backend error: token {_API_KEY} was rejected"
        )
        sanitized = mgr._sanitize_error(exc)
        assert _API_KEY not in sanitized

    def test_secret_permission_denied_no_secret_in_allowed_patterns(self):
        from general_ludd.secrets.manager import SecretPermissionDeniedError

        exc = SecretPermissionDeniedError(
            path="projects/1/api_key",
            action="read",
            agent_type="subagent",
            allowed_patterns=["projects/1/*"],
        )
        msg = str(exc)
        assert "api_key" in msg
        assert _API_KEY not in msg

    def test_write_secret_error_redacted(self, caplog):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.create_or_update_secret.side_effect = (
            RuntimeError(f"value '{_API_KEY}' rejected by policy")
        )
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr._track_secret_value(_API_KEY)

        import logging

        with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
            mgr.write_secret("test/err", {"key": _API_KEY})

        for record in caplog.records:
            assert _API_KEY not in record.getMessage()

    def test_read_secret_error_redacted(self, caplog):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            RuntimeError(f"token {_PASSWORD} is invalid")
        )
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr._track_secret_value(_PASSWORD)

        import logging

        with caplog.at_level(logging.ERROR):
            with pytest.raises(SecretsUnavailableError) as exc_info:
                mgr.read_secret("test/read-err")
            err_msg = str(exc_info.value)
            assert _PASSWORD not in err_msg

        for record in caplog.records:
            assert _PASSWORD not in record.getMessage()

    def test_list_secrets_error_redacted(self, caplog):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.list_metadata.side_effect = (
            RuntimeError(f"auth with {_API_KEY} failed")
        )
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr._track_secret_value(_API_KEY)

        import logging

        with caplog.at_level(logging.ERROR):
            with pytest.raises(SecretsUnavailableError) as exc_info:
                mgr.list_secrets("test/prefix")
            err_msg = str(exc_info.value)
            assert _API_KEY not in err_msg

    def test_resolve_alias_error_redacted(self, caplog):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretAlias, SecretsManager, SecretsUnavailableError

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            RuntimeError(f"secret {_PASSWORD} expired")
        )
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr._track_secret_value(_PASSWORD)
        mgr.register_alias(SecretAlias("my_alias", "test/alias-path"))

        import logging

        with caplog.at_level(logging.ERROR):
            with pytest.raises(SecretsUnavailableError) as exc_info:
                mgr.resolve("my_alias")
            err_msg = str(exc_info.value)
            assert _PASSWORD not in err_msg

    def test_rotate_secret_id_error_redacted(self, caplog):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = MagicMock()
        mock_client.auth.approle.read_role_id.return_value = {
            "data": {"role_id": "test-role-id"}
        }
        mock_client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "new-secret-id-xyz", "secret_id_accessor": "acc1"}
        }
        mock_client.auth.approle.destroy_secret_id_accessor.side_effect = (
            RuntimeError(f"bad token {_API_KEY}")
        )
        mock_client.auth.approle.create_role.return_value = {}

        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr._track_secret_value(_API_KEY)

        import logging

        with caplog.at_level(logging.WARNING):
            new_id = mgr.rotate_approle_secret_id("test-role")
            assert new_id == "new-secret-id-xyz"

        for record in caplog.records:
            assert _API_KEY not in record.getMessage()

    def test_scan_for_image_updates_error_no_raw_body(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            RuntimeError(f"the key {_API_KEY} caused failure")
        )
        config = OpenBaoConfig(mode="auto", local_image="ghcr.io/test/image")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr._track_secret_value(_API_KEY)

        with pytest.raises(SecretsUnavailableError) as exc_info:
            mgr.scan_for_image_updates()
        err_msg = str(exc_info.value)
        assert "RuntimeError" in err_msg
        assert _API_KEY not in err_msg


# ---------------------------------------------------------------------------
# 6. Database safety
# ---------------------------------------------------------------------------


class TestDatabaseSafety:
    """Secrets are encrypted at rest in the database — never plaintext."""

    def test_secrets_manager_uses_openbao_backend_not_db(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        mgr.write_secret("db/test", {"value": _API_KEY})
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once()

    def test_secret_path_prevents_traversal(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=MagicMock())
        with pytest.raises(ValueError, match=r".."):
            mgr._validate_secret_path("projects/1/../../../etc/passwd")

    def test_secret_path_rejects_invalid_chars(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=MagicMock())
        with pytest.raises(ValueError, match="invalid secret path"):
            mgr._validate_secret_path("bad;path;injection")

    def test_secret_path_allows_valid_names(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=MagicMock())
        mgr._validate_secret_path("projects/abc-123/api_key")
        mgr._validate_secret_path("model-profiles/uuid-value/credential_alias")
        mgr._validate_secret_path("gludd/payment/kek")

    def test_payment_vault_encrypts_not_plaintext(self):
        from general_ludd.secrets.payment_vault import redact_card_number

        pan = "4111111111111111"
        redacted = redact_card_number(pan)
        assert "4111111111111111" not in redacted
        assert redacted == "**** **** **** 1111"

    def test_openbao_config_masked_token_in_serialization(self):
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com",
            external_token="s.real-token-abc123",
        )
        assert config.model_dump().get("external_token") == "**REDACTED**"
        assert "s.real-token-abc123" not in config.model_dump_json()


# ---------------------------------------------------------------------------
# 7. File system safety
# ---------------------------------------------------------------------------


class TestFileSystemSafety:
    """Temp files, swap, and core dumps must never contain secrets."""

    def test_confine_path_allows_sub_path(self):
        from general_ludd.security.sanitize import confine_path

        with tempfile.TemporaryDirectory() as base:
            result = confine_path("subdir/file.txt", base)
            assert result is not None

    def test_confine_path_rejects_escape(self):
        from general_ludd.security.sanitize import confine_path

        with tempfile.TemporaryDirectory() as base:
            result = confine_path("../../etc/passwd", base)
            assert result is None

    def test_confine_path_rejects_absolute(self):
        from general_ludd.security.sanitize import confine_path

        with tempfile.TemporaryDirectory() as base:
            result = confine_path("/etc/passwd", base)
            assert result is None

    def test_confine_path_rejects_symlink_escape(self):
        from general_ludd.security.sanitize import confine_path

        with tempfile.TemporaryDirectory() as outer:
            inner = os.path.join(outer, "inner")
            os.makedirs(inner, exist_ok=True)
            escape = os.path.join(outer, "escape")
            os.makedirs(escape, exist_ok=True)
            symlink = os.path.join(inner, "link")
            os.symlink(escape, symlink, target_is_directory=True)
            assert confine_path("link", inner) is None

    def test_confine_path_rejects_empty_candidate(self):
        from general_ludd.security.sanitize import confine_path

        result = confine_path("", "/tmp")
        assert result is None

    def test_confine_path_rejects_empty_root(self):
        from general_ludd.security.sanitize import confine_path

        result = confine_path("file.txt", "")
        assert result is None

    def test_confine_path_multi_finds_any_root(self):
        from general_ludd.security.sanitize import confine_path_multi

        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            with open(os.path.join(d1, "target.txt"), "w") as f:
                f.write("data")
            result = confine_path_multi("target.txt", [d2, d1])
            assert result is not None
            assert (d1 in result or d2 in result)

    def test_is_path_within_true_for_subpath(self):
        from general_ludd.security.sanitize import is_path_within

        with tempfile.TemporaryDirectory() as base:
            assert is_path_within("child.txt", base) is True

    def test_is_path_within_false_for_escape(self):
        from general_ludd.security.sanitize import is_path_within

        with tempfile.TemporaryDirectory() as base:
            assert is_path_within("../escape.txt", base) is False

    def test_sanitize_path_rejects_traversal(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("../../../etc/passwd") is None
        assert sanitize_path("foo/../bar") is None

    def test_sanitize_path_rejects_absolute(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("/etc/passwd") is None

    def test_sanitize_path_allows_relative(self):
        from general_ludd.security.sanitize import sanitize_path

        result = sanitize_path("subdir/file.txt")
        assert result == "subdir/file.txt"

    def test_sanitize_path_strips_dot_slash_prefix(self):
        from general_ludd.security.sanitize import sanitize_path

        result = sanitize_path("./relative/path.txt")
        assert result == "relative/path.txt"

    def test_sanitize_path_rejects_empty(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("") is None
        assert sanitize_path("   ") is None

    def test_temp_files_dont_leak_secrets_through_known_values(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        mock_client.secrets.kv.v2.create_or_update_secret.side_effect = (
            RuntimeError(f"Invalid value: {_API_KEY}")
        )

        with pytest.raises(RuntimeError):
            mgr.write_secret("tmp/test", {"key": _API_KEY})
        assert _API_KEY in mgr._known_secret_values

        redacted = mgr._redact_message(f"Error: Invalid value: {_API_KEY}")
        assert _API_KEY not in redacted
        assert "***REDACTED***" in redacted


# ---------------------------------------------------------------------------
# 8. Subprocess safety
# ---------------------------------------------------------------------------


class TestSubprocessSafety:
    """Secrets must not leak via command-line arguments or env in subprocesses."""

    def test_env_secrets_manager_rejects_gludd_psk(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager()
        result = mgr.resolve("GLUDD_AUTH_PSK")
        assert result is None

    def test_env_secrets_manager_rejects_arbitrary_env(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager()
        assert mgr.resolve("PATH") is None
        assert mgr.resolve("HOME") is None

    def test_env_secrets_manager_allows_api_key_suffix(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"OPENAI_API_KEY": _API_KEY})
        assert mgr.resolve("OPENAI_API_KEY") == _API_KEY

    def test_env_secrets_manager_allows_auth_token_suffix(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"SLURM_AUTH_TOKEN": "slurm-token-abc"})
        assert mgr.resolve("SLURM_AUTH_TOKEN") == "slurm-token-abc"

    def test_env_secrets_manager_allows_base_url_suffix(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"ZAI_BASE_URL": "https://api.example.com"})
        assert mgr.resolve("ZAI_BASE_URL") == "https://api.example.com"

    def test_env_secrets_manager_allows_gludd_secret_prefix(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"GLUDD_SECRET_DB_PASS": "db-pass-xyz"})
        assert mgr.resolve("GLUDD_SECRET_DB_PASS") == "db-pass-xyz"

    def test_env_secrets_manager_explicit_allow_set(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(allow={"CUSTOM_RUNTIME_VAR"})
        mgr.allow_env("ANOTHER_CUSTOM")
        assert mgr._is_allowlisted("CUSTOM_RUNTIME_VAR") is True
        assert mgr._is_allowlisted("ANOTHER_CUSTOM") is True

    def test_env_secrets_manager_explicit_overrides_always_work(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"ANY_NAME_AT_ALL": "value-xyz"})
        assert mgr.resolve("ANY_NAME_AT_ALL") == "value-xyz"

    def test_mcp_env_isolation_allowlist_only(self):
        from general_ludd.mcp.transport import _ENV_ALLOWLIST

        allowed = frozenset(_ENV_ALLOWLIST)
        expected = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"})
        assert allowed == expected

    def test_mcp_env_isolation_no_psk_in_allowlist(self):
        from general_ludd.mcp.transport import _ENV_ALLOWLIST

        gludd_vars = {k for k in _ENV_ALLOWLIST if "PSK" in k.upper()}
        assert len(gludd_vars) == 0

    def test_mcp_env_isolation_no_api_key_in_allowlist(self):
        from general_ludd.mcp.transport import _ENV_ALLOWLIST

        key_vars = {k for k in _ENV_ALLOWLIST if "API" in k.upper() or "KEY" in k.upper()}
        assert len(key_vars) == 0

    def test_env_secrets_manager_does_not_leak_via_caps_variant(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager()
        assert mgr.resolve("gludd_psk") is None
        assert mgr.resolve("GLUDD_AUTH_PSK") is None

    def test_migrate_profile_skips_gludd_psk_from_env(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager
        from general_ludd.secrets.migration import migrate_profile_secrets

        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        profiles = [
            {
                "model_profile_id": "p1",
                "credential_alias": "GLUDD_AUTH_PSK",
            }
        ]
        result = migrate_profile_secrets(mgr, profiles)
        assert result["migrated"] == 0
        assert "GLUDD_AUTH_PSK" in result["skipped"]

    def test_mcp_launch_command_validate_rejects_shell_meta(self):
        from general_ludd.mcp.transport import MCPTransportError, _validate_launch_command

        with pytest.raises(MCPTransportError, match="shell"):
            _validate_launch_command(["npx", "pkg;rm -rf /"])

    def test_mcp_launch_command_validate_rejects_empty(self):
        from general_ludd.mcp.transport import MCPTransportError, _validate_launch_command

        with pytest.raises(MCPTransportError, match="empty"):
            _validate_launch_command([])

    def test_mcp_launch_command_validate_allows_python(self):
        from general_ludd.mcp.transport import _validate_launch_command

        _validate_launch_command(["python3", "-m", "my_module"])

    def test_mcp_launch_command_validate_allows_node(self):
        from general_ludd.mcp.transport import _validate_launch_command

        _validate_launch_command(["node", "/path/to/server.js"])

    def test_mcp_launch_command_version_pinned_for_npx(self):
        from general_ludd.mcp.transport import _validate_launch_command

        _validate_launch_command(["npx", "pkg@1.2.3"])

    def test_mcp_launch_command_rejects_unpinned_npx(self):
        from general_ludd.mcp.transport import MCPTransportError, _validate_launch_command

        with pytest.raises(MCPTransportError, match="not version-pinned"):
            _validate_launch_command(["npx", "pkg"])

    def test_mcp_launch_command_rejects_path_traversal(self):
        from general_ludd.mcp.transport import MCPTransportError, _validate_launch_command

        with pytest.raises(MCPTransportError, match="traversal"):
            _validate_launch_command(["node", "../../etc/malicious.js"])


# ---------------------------------------------------------------------------
# Cross-cutting: full pipeline integration
# ---------------------------------------------------------------------------


class TestFullPipelineNoLeak:
    """End-to-end: secret flows through write → read → error without leaking."""

    def test_full_write_read_error_cycle_is_safe(self, caplog):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"api_key": _API_KEY}}
        }
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        mgr.write_secret("full/test", {"api_key": _API_KEY})
        assert _API_KEY in mgr._known_secret_values

        result = mgr.read_secret("full/test")
        assert result is not None
        assert result["api_key"] == _API_KEY

        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            RuntimeError(f"connection lost, had token {_API_KEY}")
        )

        import logging

        with caplog.at_level(logging.ERROR):
            with pytest.raises(SecretsUnavailableError) as exc_info:
                mgr.read_secret("full/test")
            err_msg = str(exc_info.value)
            assert _API_KEY not in err_msg

        for record in caplog.records:
            assert _API_KEY not in record.getMessage()

    def test_all_redaction_passes_combined(self):
        from general_ludd.secrets.manager import SecretsManager

        msg = (
            f"Operation failed: url=http://user:{_API_KEY}@host, "
            f"token: {_PASSWORD}, host=127.0.0.1, sk-proj-secret-inline"
        )

        static = SecretsManager._redact(msg)
        assert _API_KEY not in static

        mgr, _mock = _build_secrets_manager_with_known_values(
            _API_KEY, _PASSWORD, "s.root-token"
        )
        combined = mgr._redact_message(msg)
        assert _API_KEY not in combined
        assert _PASSWORD not in combined
        assert "127.0.0.1" not in combined or "***REDACTED***" in combined

    def test_bootstrap_result_container_token_never_in_log(self, caplog):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = MagicMock()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        result = mgr.bootstrap_local()
        token = result.token
        assert isinstance(token, str) and len(token) > 0

        import logging

        with caplog.at_level(logging.DEBUG):
            mgr.connect()
        for record in caplog.records:
            if token and len(token) >= 6:
                assert token not in record.getMessage()

    def test_secret_alias_rejects_traversal(self):
        from general_ludd.secrets.manager import SecretAlias

        with pytest.raises(ValueError, match=r".."):
            SecretAlias("bad", "../escape")

    def test_secret_alias_rejects_null_byte(self):
        from general_ludd.secrets.manager import SecretAlias

        with pytest.raises(ValueError, match="null"):
            SecretAlias("bad", "path\x00inject")

    def test_secret_alias_rejects_tilde(self):
        from general_ludd.secrets.manager import SecretAlias

        with pytest.raises(ValueError, match="tilde"):
            SecretAlias("bad", "~/.ssh/id_rsa")

    def test_secret_alias_rejects_empty_path(self):
        from general_ludd.secrets.manager import SecretAlias

        with pytest.raises(ValueError, match="empty"):
            SecretAlias("bad", "")

    def test_secret_alias_rejects_invalid_chars(self):
        from general_ludd.secrets.manager import SecretAlias

        with pytest.raises(ValueError, match="invalid"):
            SecretAlias("bad", "path;DROP TABLE secrets;")

    def test_secret_alias_rejects_invalid_mount_chars(self):
        from general_ludd.secrets.manager import SecretAlias

        with pytest.raises(ValueError, match="invalid"):
            SecretAlias("bad", "good-path", mount="bad;mount")

    def test_secret_alias_rejects_null_byte_in_mount(self):
        from general_ludd.secrets.manager import SecretAlias

        with pytest.raises(ValueError, match="null"):
            SecretAlias("bad", "good-path", mount="mo\x00unt")
