"""
Test that scan_for_image_updates() sanitizes exc str from SecretsUnavailableError message.
The raised error must NOT contain the raw exception body (which could hold secrets),
and MUST contain the exception class name.
"""
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

LEAKED_SECRET_BODY = "supersecret-token-abc123"  # pragma: allowlist secret


class _LeakyError(Exception):
    """Exception whose str() contains a secret-like payload."""

    def __str__(self) -> str:
        return f"connection failed: password={LEAKED_SECRET_BODY}"


def test_scan_for_image_updates_exc_message_sanitized() -> None:
    """scan_for_image_updates must not leak raw exception text into SecretsUnavailableError."""
    manager = MagicMock()
    # scan_for_image_updates reads image_ref from self._config.local_image
    manager._config.local_image = "registry.example.com/myimage:latest"
    # Make read_secret raise our leaky exception (not SecretsUnavailableError,
    # not a genuine-not-found) so the except branch re-raises as SecretsUnavailableError.
    manager.read_secret.side_effect = _LeakyError("boom")

    with patch(
        "general_ludd.secrets.manager._is_genuine_not_found", return_value=False
    ), pytest.raises(SecretsUnavailableError) as exc_info:
        SecretsManager.scan_for_image_updates(manager)

    msg = str(exc_info.value)
    assert LEAKED_SECRET_BODY not in msg, (
        f"Raw exception body leaked into SecretsUnavailableError: {msg!r}"
    )
    assert "_LeakyError" in msg, (
        f"Exception class name missing from SecretsUnavailableError: {msg!r}"
    )
