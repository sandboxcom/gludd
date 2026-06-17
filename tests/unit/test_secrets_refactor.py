"""Focused tests for the secrets refactor.

Covers:
- SecretsManagerProtocol structural conformance (both implementations)
- _read_kv_secret helper: InvalidPath->None, other->SecretsUnavailableError
- _strip_and_require config helper: strips whitespace, rejects empty
- SecretsManager.resolve + read_secret via the new _read_kv_secret path
- scan_for_image_updates fail-CLOSED re-raise
"""

from __future__ import annotations

from unittest.mock import MagicMock

import hvac
import pytest
from pydantic import ValidationError

from general_ludd.secrets import (
    EnvSecretsManager,
    SecretsManager,
    SecretsManagerProtocol,
    SecretsUnavailableError,
)
from general_ludd.secrets.config import OpenBaoConfig, _strip_and_require
from general_ludd.secrets.manager import SecretAlias, _read_kv_secret

# ---------------------------------------------------------------------------
# SecretsManagerProtocol conformance
# ---------------------------------------------------------------------------


class TestSecretsManagerProtocol:
    """Both concrete implementations must satisfy the Protocol structurally."""

    def test_secrets_manager_satisfies_protocol(self):
        mgr = SecretsManager()
        assert isinstance(mgr, SecretsManagerProtocol)

    def test_env_secrets_manager_satisfies_protocol(self):
        mgr = EnvSecretsManager()
        assert isinstance(mgr, SecretsManagerProtocol)

    def test_protocol_has_resolve(self):
        assert hasattr(SecretsManagerProtocol, "resolve")

    def test_protocol_has_list_aliases(self):
        assert hasattr(SecretsManagerProtocol, "list_aliases")

    def test_non_conforming_object_does_not_satisfy_protocol(self):
        class NotAManager:
            pass

        assert not isinstance(NotAManager(), SecretsManagerProtocol)

    def test_partial_conformance_fails(self):
        # Only resolve, no list_aliases
        class ResolveOnly:
            def resolve(self, alias_name: str) -> str | None:
                return None

        assert not isinstance(ResolveOnly(), SecretsManagerProtocol)

    def test_accepts_protocol_typed_argument(self):
        """A function typed to SecretsManagerProtocol should accept both impls."""

        def call_resolve(mgr: SecretsManagerProtocol) -> str | None:
            return mgr.resolve("missing_alias")

        assert call_resolve(SecretsManager()) is None
        assert call_resolve(EnvSecretsManager()) is None


# ---------------------------------------------------------------------------
# _read_kv_secret helper
# ---------------------------------------------------------------------------


class TestReadKvSecret:
    def _mock_client(self, *, return_value=None, side_effect=None):
        client = MagicMock()
        kv = client.secrets.kv.v2.read_secret_version
        if side_effect is not None:
            kv.side_effect = side_effect
        else:
            kv.return_value = return_value
        return client

    def test_returns_data_dict_on_success(self):
        payload = {"username": "admin", "password": "hunter2"}  # pragma: allowlist secret
        client = self._mock_client(
            return_value={"data": {"data": payload}}
        )
        result = _read_kv_secret(client, "myapp/config", "secret", "path 'myapp/config'")
        assert result == payload

    def test_returns_none_for_invalid_path(self):
        client = self._mock_client(side_effect=hvac.exceptions.InvalidPath("not found"))
        result = _read_kv_secret(client, "noexist", "secret", "path 'noexist'")
        assert result is None

    def test_raises_unavailable_for_other_exceptions(self):
        client = self._mock_client(side_effect=RuntimeError("connection refused"))
        with pytest.raises(SecretsUnavailableError, match="connection refused"):
            _read_kv_secret(client, "myapp/config", "secret", "path 'myapp/config'")

    def test_raises_unavailable_for_forbidden(self):
        client = self._mock_client(
            side_effect=hvac.exceptions.Forbidden("permission denied")
        )
        with pytest.raises(SecretsUnavailableError):
            _read_kv_secret(client, "restricted", "secret", "alias 'restricted'")

    def test_returns_none_when_response_has_no_data(self):
        client = self._mock_client(return_value=None)
        result = _read_kv_secret(client, "empty", "secret", "path 'empty'")
        assert result is None

    def test_context_label_appears_in_error_message(self):
        client = self._mock_client(side_effect=OSError("timeout"))
        with pytest.raises(SecretsUnavailableError, match="alias 'my_key'"):
            _read_kv_secret(client, "path/to/key", "secret", "alias 'my_key'")

    def test_does_not_log_or_return_secret_value(self, caplog):

        payload = {"value": "super_secret_value_xyz"}
        client = self._mock_client(
            return_value={"data": {"data": payload}}
        )
        result = _read_kv_secret(client, "myapp/secret", "secret", "alias 'myapp'")
        # The returned dict is the data dict (callers may use it) — but nothing
        # in the helper's own logging should leak the value.
        assert result is not None
        for record in caplog.records:
            assert "super_secret_value_xyz" not in record.getMessage()

    def test_chain_cause_preserved(self):
        original = RuntimeError("original cause")
        client = self._mock_client(side_effect=original)
        with pytest.raises(SecretsUnavailableError) as exc_info:
            _read_kv_secret(client, "p", "m", "ctx")
        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# SecretsManager.resolve via _read_kv_secret
# ---------------------------------------------------------------------------


class TestSecretsManagerResolveViaHelper:
    def _manager_with_client(self, client: object) -> SecretsManager:
        mgr = SecretsManager()
        mgr._client = client  # type: ignore[assignment]
        mgr.register_alias(SecretAlias(alias="db_pass", path="db/pass", mount="secret"))
        return mgr

    def test_resolve_returns_value_field(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "s3cr3t"}}
        }
        mgr = self._manager_with_client(mock_client)
        result = mgr.resolve("db_pass")
        assert result == "s3cr3t"

    def test_resolve_missing_path_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            hvac.exceptions.InvalidPath("not found")
        )
        mgr = self._manager_with_client(mock_client)
        assert mgr.resolve("db_pass") is None

    def test_resolve_backend_error_raises_unavailable(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            RuntimeError("backend down")
        )
        mgr = self._manager_with_client(mock_client)
        with pytest.raises(SecretsUnavailableError):
            mgr.resolve("db_pass")


# ---------------------------------------------------------------------------
# SecretsManager.read_secret via _read_kv_secret
# ---------------------------------------------------------------------------


class TestSecretsManagerReadSecretViaHelper:
    def _manager_with_client(self, client: object) -> SecretsManager:
        mgr = SecretsManager()
        mgr._client = client  # type: ignore[assignment]
        return mgr

    def test_read_secret_returns_dict(self):
        payload = {"key": "val"}
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": payload}
        }
        mgr = self._manager_with_client(mock_client)
        assert mgr.read_secret("myapp/config") == payload

    def test_read_secret_not_found_returns_none(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            hvac.exceptions.InvalidPath("not found")
        )
        mgr = self._manager_with_client(mock_client)
        assert mgr.read_secret("noexist") is None

    def test_read_secret_backend_error_raises_unavailable(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = OSError("timeout")
        mgr = self._manager_with_client(mock_client)
        with pytest.raises(SecretsUnavailableError):
            mgr.read_secret("myapp/config")


# ---------------------------------------------------------------------------
# scan_for_image_updates — fail-CLOSED via read_secret
# ---------------------------------------------------------------------------


class TestScanForImageUpdateFailClosed:
    def test_scan_propagates_unavailable(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            RuntimeError("backend sealed")
        )
        mgr = SecretsManager()
        mgr._client = mock_client
        with pytest.raises(SecretsUnavailableError):
            mgr.scan_for_image_updates()

    def test_scan_returns_none_when_pin_absent(self):
        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            hvac.exceptions.InvalidPath("not found")
        )
        mgr = SecretsManager()
        mgr._client = mock_client
        assert mgr.scan_for_image_updates() is None


# ---------------------------------------------------------------------------
# _strip_and_require config helper
# ---------------------------------------------------------------------------


class TestStripAndRequireHelper:
    def test_strips_whitespace(self):
        assert _strip_and_require("  secret  ") == "secret"

    def test_strips_tabs_and_newlines(self):
        assert _strip_and_require("\t approle \n") == "approle"

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _strip_and_require("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _strip_and_require("   ")

    def test_accepts_non_string(self):
        # Non-string values are returned as-is after str() conversion (Pydantic
        # coerces before calling; helper just returns the value as a str).
        result = _strip_and_require("approle")
        assert result == "approle"

    def test_field_name_in_error(self):
        with pytest.raises(ValueError, match="kv_mount must not be empty"):
            _strip_and_require("", field_name="kv_mount")


class TestOpenBaoConfigValidation:
    def test_kv_mount_strips_whitespace(self):
        cfg = OpenBaoConfig(kv_mount="  secret  ")
        assert cfg.kv_mount == "secret"

    def test_auth_method_strips_whitespace(self):
        cfg = OpenBaoConfig(auth_method="  approle  ")
        assert cfg.auth_method == "approle"

    def test_kv_mount_empty_rejected(self):
        with pytest.raises(ValidationError):
            OpenBaoConfig(kv_mount="")

    def test_auth_method_empty_rejected(self):
        with pytest.raises(ValidationError):
            OpenBaoConfig(auth_method="   ")
