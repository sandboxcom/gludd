"""Security regression tests for SecretsManager.

Fix A: resolve() must not interpolate exc into log/raised message (secret leak).
Fix B: register_alias() must validate path and mount (path traversal / arbitrary backend read).
"""

from __future__ import annotations

import logging

import pytest

from general_ludd.secrets.manager import SecretAlias, SecretsManager, SecretsUnavailableError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeBackendError(Exception):
    """Simulates an hvac error whose message contains sensitive response body."""


class _FakeKvV2:
    def read_secret_version(self, *, path: str, mount_point: str) -> dict:
        raise _FakeBackendError("HTTP 403 body: token=LEAKED_SECRET_BODY api_key=sk-abc")


class _FakeSecrets:
    kv = type("kv", (), {"v2": _FakeKvV2()})()


class _FakeClient:
    secrets = _FakeSecrets()


# ---------------------------------------------------------------------------
# Fix A — secret leak via exception
# ---------------------------------------------------------------------------

def test_resolve_does_not_leak_exception_body_in_raised_error() -> None:
    """resolve() must not include the backend exception body in the raised error."""
    manager = SecretsManager(client=_FakeClient())
    manager._aliases["myalias"] = SecretAlias("myalias", "app/db", "secret")

    with pytest.raises(SecretsUnavailableError) as exc_info:
        manager.resolve("myalias")

    msg = str(exc_info.value)
    assert "LEAKED_SECRET_BODY" not in msg, f"secret body leaked into raised error: {msg!r}"
    assert "_FakeBackendError" in msg, f"exception class name missing from error: {msg!r}"


def test_resolve_does_not_leak_exception_body_in_log(caplog: pytest.LogCaptureFixture) -> None:
    """resolve() must not include the backend exception body in the log record."""
    manager = SecretsManager(client=_FakeClient())
    manager._aliases["myalias"] = SecretAlias("myalias", "app/db", "secret")

    with caplog.at_level(logging.ERROR, logger="general_ludd.secrets.manager"), pytest.raises(SecretsUnavailableError):
        manager.resolve("myalias")

    for record in caplog.records:
        msg = record.getMessage()
        assert "LEAKED_SECRET_BODY" not in msg, f"secret body leaked into log: {msg!r}"


# ---------------------------------------------------------------------------
# Fix B — path traversal / arbitrary mount
# ---------------------------------------------------------------------------

def test_register_alias_rejects_path_traversal_dotdot() -> None:
    """Absolute path traversal via leading '..' must be rejected."""
    manager = SecretsManager()
    with pytest.raises(ValueError, match="invalid secret path"):
        manager.register_alias(SecretAlias("alias", "../../etc/passwd", "secret"))


def test_register_alias_rejects_path_with_dotdot_segment() -> None:
    """Embedded '..' segment must be rejected even when path starts validly."""
    manager = SecretsManager()
    with pytest.raises(ValueError, match="invalid secret path"):
        manager.register_alias(SecretAlias("alias", "a/../../b", "secret"))


def test_register_alias_rejects_unpermitted_mount() -> None:
    """A mount not in _PERMITTED_MOUNTS must be rejected."""
    manager = SecretsManager()
    with pytest.raises(ValueError, match="not in permitted mounts"):
        manager.register_alias(SecretAlias("alias", "app/db", "evil"))


def test_register_alias_accepts_valid_path_and_mount() -> None:
    """A well-formed path and permitted mount must succeed and be stored."""
    manager = SecretsManager()
    manager.register_alias(SecretAlias("mydb", "app/db", "secret"))
    assert "mydb" in manager.list_aliases()
