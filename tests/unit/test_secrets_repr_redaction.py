"""Regression: secret-bearing dataclasses must not leak credentials via repr().

repr() output lands in logs, tracebacks, and debug dumps; a token / secret_id
appearing there is a credential-leak vector. BootstrapResult.token /
container_token and AppRoleCreds.secret_id are marked field(repr=False) while
remaining ordinary (required) attributes.
"""

from __future__ import annotations

from general_ludd.secrets.manager import AppRoleCreds, BootstrapResult


def test_bootstrap_result_repr_hides_tokens() -> None:
    r = BootstrapResult(
        url="https://vault.local",
        token="s.SUPERSECRET-root-token",  # noqa: S106 - test literal, not a real secret
        initialized=True,
        container_token="s.CONTAINER-secret",  # noqa: S106 - test literal
    )
    text = repr(r)
    assert "SUPERSECRET" not in text
    assert "CONTAINER-secret" not in text
    # Non-secret fields stay visible for debuggability.
    assert "vault.local" in text
    assert "initialized=True" in text
    # The values remain accessible as attributes (repr-hidden, not dropped).
    assert r.token == "s.SUPERSECRET-root-token"
    assert r.container_token == "s.CONTAINER-secret"


def test_approle_creds_repr_hides_secret_id() -> None:
    c = AppRoleCreds(role_id="role-123", secret_id="s.APPROLE-SECRET")  # noqa: S106
    text = repr(c)
    assert "APPROLE-SECRET" not in text
    assert "role-123" in text  # role_id is not a secret
    assert c.secret_id == "s.APPROLE-SECRET"
