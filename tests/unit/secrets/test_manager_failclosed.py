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
