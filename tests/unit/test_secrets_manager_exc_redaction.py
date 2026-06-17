"""Tests that SecretsManager does not leak exception bodies into error messages."""

from __future__ import annotations

import pytest

from general_ludd.secrets.manager import SecretAlias, SecretsManager, SecretsUnavailableError


class _SecretLeakError(Exception):
    """Fake hvac exception whose str() embeds a secret value."""

    def __str__(self) -> str:
        return "connection failed: token=TOPSECRET_TOKEN_VALUE path=/secret/data/foo"


class _FailingKvV2:
    @staticmethod
    def read_secret_version(**kwargs: object) -> object:
        raise _SecretLeakError("bad")


class _FailingKv:
    v2 = _FailingKvV2()


class _FailingSecrets:
    kv = _FailingKv()


class _FailingClient:
    """Fake hvac client that raises _SecretLeakError on any secrets call."""

    secrets = _FailingSecrets()


def _make_manager() -> SecretsManager:
    mgr = SecretsManager(client=_FailingClient())
    mgr.register_alias(SecretAlias(alias="myalias", path="foo/bar", mount="secret"))
    return mgr


def test_resolve_raised_error_does_not_contain_secret_body() -> None:
    mgr = _make_manager()
    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.resolve("myalias")
    error_msg = str(exc_info.value)
    assert "TOPSECRET_TOKEN_VALUE" not in error_msg


def test_resolve_raised_error_contains_exc_type_name() -> None:
    mgr = _make_manager()
    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.resolve("myalias")
    error_msg = str(exc_info.value)
    assert "_SecretLeakError" in error_msg


def test_resolve_chains_original_exception() -> None:
    mgr = _make_manager()
    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.resolve("myalias")
    assert isinstance(exc_info.value.__cause__, _SecretLeakError)


def test_read_secret_raised_error_does_not_contain_secret_body() -> None:
    mgr = SecretsManager(client=_FailingClient())
    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.read_secret("some/path")
    error_msg = str(exc_info.value)
    assert "TOPSECRET_TOKEN_VALUE" not in error_msg


def test_read_secret_raised_error_contains_exc_type_name() -> None:
    mgr = SecretsManager(client=_FailingClient())
    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.read_secret("some/path")
    error_msg = str(exc_info.value)
    assert "_SecretLeakError" in error_msg


def test_read_secret_chains_original_exception() -> None:
    mgr = SecretsManager(client=_FailingClient())
    with pytest.raises(SecretsUnavailableError) as exc_info:
        mgr.read_secret("some/path")
    assert isinstance(exc_info.value.__cause__, _SecretLeakError)
