"""Tests for secret-value sanitization in resolve() error logs.

Covers the SecretAlias.resolve() path where backend exceptions may carry
secret material in their message bodies. Verifies that the sanitization
helpers (_sanitize_error / _redact) prevent leakage.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.secrets.manager import SecretAlias, SecretsManager, SecretsUnavailableError

LEAKED_TOKEN = "super-secret-token-abc123xyz789"  # pragma: allowlist secret


class _BackendError(RuntimeError):
    """A non-NotFound exception whose message contains a secret value."""


def test_resolve_exc_message_sanitized():
    """resolve() log and error message must NOT leak the raw exception body."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = _BackendError(
        f"vault error with token={LEAKED_TOKEN}"
    )

    mgr = SecretsManager(client=client)
    mgr.register_alias(SecretAlias("myalias", "prod/db-creds"))

    with (
        patch("general_ludd.secrets.manager.logger.error") as mock_error,
        pytest.raises(SecretsUnavailableError) as exc_info,
    ):
        mgr.resolve("myalias")

    assert LEAKED_TOKEN not in str(exc_info.value), (
        "SecretsUnavailableError message leaked raw exception body"
    )
    assert "_BackendError" in str(exc_info.value), (
        "error message should contain exception class name"
    )

    mock_error.assert_called_once()
    log_text = " ".join(str(a) for a in mock_error.call_args[0])
    assert LEAKED_TOKEN not in log_text, (
        "log message leaked raw exception body"
    )
    assert "_BackendError" in log_text, (
        "log message should contain exception class name"
    )


def test_resolve_exc_sanitized_path_still_present():
    """The alias_name should still appear so operators can identify the failure."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = _BackendError(
        f"token={LEAKED_TOKEN}"
    )

    mgr = SecretsManager(client=client)
    mgr.register_alias(SecretAlias("prod-db", "prod/db-creds"))

    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.resolve("prod-db")

    msg = str(exc_info.value)
    assert LEAKED_TOKEN not in msg
    assert "prod-db" in msg, "alias_name should still appear in error message"


def test_resolve_genuine_not_found_returns_none():
    """Genuine 404 (InvalidPath) is absence — no error log, no exception."""
    from hvac.exceptions import InvalidPath

    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = InvalidPath(
        "secret not found"
    )

    mgr = SecretsManager(client=client)
    mgr.register_alias(SecretAlias("missing", "nonexistent/path"))

    with patch("general_ludd.secrets.manager.logger.error") as mock_error:
        result = mgr.resolve("missing")

    assert result is None
    mock_error.assert_not_called()


def test_resolve_success_returns_value():
    """Happy path: resolve() returns the secret value."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"value": "resolved-secret-value"}}
    }

    mgr = SecretsManager(client=client)
    mgr.register_alias(SecretAlias("good", "prod/db-creds"))

    result = mgr.resolve("good")
    assert result == "resolved-secret-value"


def test_resolve_no_alias_returns_none():
    """Unknown alias returns None without touching the backend."""
    mgr = SecretsManager()
    result = mgr.resolve("nonexistent-alias")
    assert result is None
