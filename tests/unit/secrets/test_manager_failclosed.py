"""Fail-closed behaviour for the secrets backend.

Security invariant: an outage / auth failure / sealed backend / TLS error must
NOT masquerade as a missing secret (which would let callers fail OPEN). Only a
genuine 404 (hvac.exceptions.InvalidPath) may be reported as absence (None).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import hvac
import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    SecretAlias,
    SecretsManager,
    SecretsUnavailableError,
    _is_genuine_not_found,
)


def _client_raising(exc: BaseException) -> MagicMock:
    """A mock hvac client whose KV reads raise ``exc``."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.side_effect = exc
    return client


# --- _is_genuine_not_found -------------------------------------------------


def test_is_genuine_not_found_only_true_for_invalid_path() -> None:
    assert _is_genuine_not_found(hvac.exceptions.InvalidPath("nope")) is True
    assert _is_genuine_not_found(hvac.exceptions.Forbidden("403")) is False
    assert _is_genuine_not_found(hvac.exceptions.VaultDown("sealed")) is False
    assert _is_genuine_not_found(ConnectionError("timeout")) is False
    assert _is_genuine_not_found(RuntimeError("boom")) is False


# --- resolve() -------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        hvac.exceptions.VaultDown("sealed"),
        hvac.exceptions.Forbidden("403"),
        hvac.exceptions.InternalServerError("500"),
        ConnectionError("connection refused"),
    ],
)
def test_resolve_raises_on_outage_not_none(exc: BaseException) -> None:
    mgr = SecretsManager(client=_client_raising(exc))
    mgr.register_alias(SecretAlias("api", "path/to/api"))
    with pytest.raises(SecretsUnavailableError):
        mgr.resolve("api")


def test_resolve_returns_none_on_genuine_not_found() -> None:
    mgr = SecretsManager(
        client=_client_raising(hvac.exceptions.InvalidPath("404"))
    )
    mgr.register_alias(SecretAlias("api", "path/to/api"))
    assert mgr.resolve("api") is None


# --- read_secret() ---------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        hvac.exceptions.VaultDown("sealed"),
        hvac.exceptions.Forbidden("403"),
        ConnectionError("connection refused"),
    ],
)
def test_read_secret_raises_on_outage_not_none(exc: BaseException) -> None:
    mgr = SecretsManager(client=_client_raising(exc))
    with pytest.raises(SecretsUnavailableError):
        mgr.read_secret("some/path")


def test_read_secret_returns_none_on_genuine_not_found() -> None:
    mgr = SecretsManager(
        client=_client_raising(hvac.exceptions.InvalidPath("404"))
    )
    assert mgr.read_secret("some/path") is None


# --- not-connected guard: SecretsUnavailableError, not RuntimeError --------


def test_read_secret_not_connected_raises_secrets_unavailable() -> None:
    """read_secret must raise SecretsUnavailableError, not RuntimeError, when
    no client has been connected.  Fail-closed callers catching
    SecretsUnavailableError would silently miss a bare RuntimeError."""
    mgr = SecretsManager()  # no client
    with pytest.raises(SecretsUnavailableError):
        mgr.read_secret("some/path")


def test_read_secret_not_connected_does_not_raise_runtime_error() -> None:
    """Explicit negative: must NOT surface RuntimeError for not-connected."""
    mgr = SecretsManager()  # no client
    with pytest.raises(SecretsUnavailableError):
        mgr.read_secret("any/path")
    # If we reach here without RuntimeError the guard is correct.


def test_close_releases_only_manager_owned_hvac_client(monkeypatch) -> None:
    owned_client = MagicMock()
    monkeypatch.setattr(hvac, "Client", MagicMock(return_value=owned_client))
    manager = SecretsManager()
    manager.bootstrap_local()
    manager.connect()

    manager.close()
    manager.close()

    owned_client.adapter.close.assert_called_once_with()

    injected = MagicMock()
    SecretsManager(client=injected).close()
    injected.adapter.close.assert_not_called()


def test_scan_for_image_updates_not_connected_raises_secrets_unavailable() -> None:
    """scan_for_image_updates calls read_secret, so a not-connected manager
    must also surface SecretsUnavailableError rather than RuntimeError."""
    mgr = SecretsManager()  # no client
    with pytest.raises(SecretsUnavailableError):
        mgr.scan_for_image_updates()


# --- error message content -------------------------------------------------


def test_resolve_error_message_contains_resolving_alias() -> None:
    """resolve() error message must say 'resolving alias' so callers logging the
    error can distinguish it from a read_secret failure."""
    exc = hvac.exceptions.VaultDown("sealed")
    mgr = SecretsManager(client=_client_raising(exc))
    mgr.register_alias(SecretAlias("mykey", "path/to/mykey"))
    with pytest.raises(SecretsUnavailableError, match="resolving alias"):
        mgr.resolve("mykey")


def test_read_secret_error_message_contains_reading() -> None:
    """read_secret() error message must say 'reading' (not 'path') so the
    context label matches the documented behaviour."""
    exc = hvac.exceptions.Forbidden("403")
    mgr = SecretsManager(client=_client_raising(exc))
    with pytest.raises(SecretsUnavailableError, match="reading"):
        mgr.read_secret("some/path")


def test_read_secret_not_connected_message_contains_reading() -> None:
    """The not-connected guard in read_secret must also embed 'reading' in the
    error message so callers get consistent context."""
    mgr = SecretsManager()  # no client
    with pytest.raises(SecretsUnavailableError, match="reading"):
        mgr.read_secret("my/secret")


# --- scan_for_image_updates() ----------------------------------------------


def test_scan_for_image_updates_raises_on_outage() -> None:
    mgr = SecretsManager(
        client=_client_raising(hvac.exceptions.VaultDown("sealed"))
    )
    with pytest.raises(SecretsUnavailableError):
        mgr.scan_for_image_updates()


def test_scan_for_image_updates_returns_none_on_genuine_not_found() -> None:
    mgr = SecretsManager(
        client=_client_raising(hvac.exceptions.InvalidPath("404"))
    )
    # No pin exists -> genuine absence -> no candidate, no raise.
    assert mgr.scan_for_image_updates() is None


# --- connect(): external transport / TLS -----------------------------------


def test_connect_refuses_plaintext_external_url() -> None:
    cfg = OpenBaoConfig(
        mode="external",
        external_url="http://vault.internal:8200",
        external_token="s.plaintext-token",
    )
    mgr = SecretsManager(config=cfg)
    with pytest.raises(SecretsUnavailableError):
        mgr.connect()


def test_connect_https_passes_explicit_tls_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_client(**kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(hvac, "Client", _fake_client)

    cfg = OpenBaoConfig(
        mode="external",
        external_url="https://vault.internal:8200",
        external_token="s.token",
        external_tls_verify="/etc/ssl/ca.pem",
    )
    mgr = SecretsManager(config=cfg)
    mgr.connect()

    assert captured["url"] == "https://vault.internal:8200"
    assert captured["token"] == "s.token"
    assert captured["verify"] == "/etc/ssl/ca.pem"


def test_connect_https_defaults_tls_verify_true(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_client(**kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(hvac, "Client", _fake_client)

    cfg = OpenBaoConfig(
        mode="external",
        external_url="https://vault.internal:8200",
        external_token="s.token",
    )
    mgr = SecretsManager(config=cfg)
    mgr.connect()

    assert captured["verify"] is True


# --- scan_for_image_updates(): exc-sanitize regression (4th site) -----------


class _LeakyBackendError(Exception):
    """Simulates a backend exception whose message contains secret material."""


def test_scan_for_image_updates_does_not_leak_exc_body_in_raised_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scan_for_image_updates() must not interpolate the raw exception body into
    the raised SecretsUnavailableError message.  A hvac/openbao exception may
    carry secret material (tokens, response bodies) in its str(); only the class
    name is safe to surface.

    Regression test for the 4th raw-exc leak site in manager.py
    (scan_for_image_updates ~L309-311).
    """
    # Patch read_secret so it raises a non-InvalidPath exception whose str()
    # contains a fake secret body — simulating an hvac error with leaked material.
    def _raise_leaky(*_args: object, **_kwargs: object) -> None:
        raise _LeakyBackendError("token=LEAKED_SECRET_BODY api_key=sk-abc123")

    mgr = SecretsManager(client=MagicMock())
    monkeypatch.setattr(mgr, "read_secret", _raise_leaky)

    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.scan_for_image_updates()

    msg = str(exc_info.value)
    assert "LEAKED_SECRET_BODY" not in msg, (
        f"raw exception body leaked into raised error: {msg!r}"
    )
    assert "_LeakyBackendError" in msg, (
        f"exception class name missing from raised error: {msg!r}"
    )
    # The non-secret image_ref must still appear so operators can diagnose which
    # image triggered the failure.
    image_ref = mgr._config.local_image
    assert image_ref in msg, (
        f"image_ref {image_ref!r} missing from raised error: {msg!r}"
    )
