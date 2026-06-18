"""Regression tests: raw exception bodies must not leak into log messages or errors.

Covers the sanitization invariant introduced in manager.py where
``read_secret`` and ``rotate_approle_secret_id`` use ``type(exc).__name__``
instead of the raw exception (which may contain secret material from vault
error bodies) — matching the same pattern already in ``resolve()``.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

LEAKED_SECRET_BODY = "super-secret-token-abc123"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# read_secret — exception branch
# ---------------------------------------------------------------------------


class _ReadError(RuntimeError):
    """A non-InvalidPath exception whose message contains a secret body."""


def test_read_secret_exc_message_sanitized(caplog):
    """SecretsUnavailableError raised by read_secret must not contain the raw
    exception body (which could embed vault secret material)."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = _ReadError(
        f"vault responded: {LEAKED_SECRET_BODY}"
    )

    mgr = SecretsManager(client=client)

    with (
        caplog.at_level(logging.ERROR, logger="general_ludd.secrets.manager"),
        pytest.raises(SecretsUnavailableError) as exc_info,
    ):
        mgr.read_secret("some/path")

    # The raised error message must NOT contain the raw secret body.
    assert LEAKED_SECRET_BODY not in str(exc_info.value), (
        "SecretsUnavailableError message leaked raw exception body"
    )
    # It SHOULD contain the exception class name (safe, structural info only).
    assert "_ReadError" in str(exc_info.value), (
        "SecretsUnavailableError message should contain exception class name"
    )

    # The log record must also NOT contain the raw secret body.
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert LEAKED_SECRET_BODY not in log_text, (
        "log message leaked raw exception body"
    )
    # Log should contain the exception class name.
    assert "_ReadError" in log_text, (
        "log message should contain exception class name"
    )


def test_read_secret_exc_sanitized_path_still_present(caplog):
    """The path being read should remain in the SecretsUnavailableError message
    so operators can identify which secret failed without seeing secret data."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = _ReadError(
        f"token={LEAKED_SECRET_BODY}"
    )

    mgr = SecretsManager(client=client)

    with (
        caplog.at_level(logging.ERROR, logger="general_ludd.secrets.manager"),
        pytest.raises(SecretsUnavailableError) as exc_info,
    ):
        mgr.read_secret("prod/db-creds")

    msg = str(exc_info.value)
    assert LEAKED_SECRET_BODY not in msg
    assert "prod/db-creds" in msg, "path should still appear in error message"


# ---------------------------------------------------------------------------
# rotate_approle_secret_id — accessor-destruction warning branch
# ---------------------------------------------------------------------------


class _DestroyError(RuntimeError):
    """A vault error whose message contains a secret body."""


def test_rotate_approle_warning_sanitized(caplog):
    """Warning logged when destroy_secret_id_accessor fails must not contain
    the raw exception body."""
    client = MagicMock()
    # generate_secret_id returns a fresh secret_id + accessor
    client.auth.approle.generate_secret_id.return_value = {
        "data": {"secret_id": "new-sid-xyz", "secret_id_accessor": "new-accessor"}
    }
    # Destroying the old accessor raises with a secret in the message
    client.auth.approle.destroy_secret_id_accessor.side_effect = _DestroyError(
        f"auth failed, token={LEAKED_SECRET_BODY}"
    )

    mgr = SecretsManager(client=client)
    # Pre-populate an accessor so there is something to destroy
    mgr._secret_id_accessors["myrole"] = ["old-accessor-1"]

    with caplog.at_level(logging.WARNING, logger="general_ludd.secrets.manager"):
        new_sid = mgr.rotate_approle_secret_id("myrole")

    assert new_sid == "new-sid-xyz"

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert LEAKED_SECRET_BODY not in log_text, (
        "rotate warning logged raw exception body"
    )
    assert "_DestroyError" in log_text, (
        "rotate warning should log exception class name"
    )


def test_rotate_approle_no_warning_when_no_old_accessors(caplog):
    """When there are no prior accessors no warning should be emitted."""
    client = MagicMock()
    client.auth.approle.generate_secret_id.return_value = {
        "data": {"secret_id": "new-sid-abc", "secret_id_accessor": "acc-abc"}
    }

    mgr = SecretsManager(client=client)
    # No pre-existing accessors

    with caplog.at_level(logging.WARNING, logger="general_ludd.secrets.manager"):
        mgr.rotate_approle_secret_id("emptyrole")

    assert not caplog.records, "no warning expected when no old accessors to destroy"
