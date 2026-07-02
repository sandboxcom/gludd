"""Secrets must never leak through a dataclass ``repr`` into logs/tracebacks.

Three frozen/plain dataclasses carry bearer-grade secrets in plain fields:

  * ``AuthPosture.psk``       — the raw pre-shared key.
  * ``StsToken.token_id``     — the opaque bearer token.
  * ``WebhookConfig.headers`` — outbound auth headers.

Each of those fields is marked ``field(repr=False)`` so the value is omitted
from the default ``repr`` (and therefore from f-string interpolation, logging,
and exception rendering). These tests pin that contract: the secret sentinel
must be ABSENT from ``repr()``/``str()``.
"""

from __future__ import annotations

from general_ludd.events.hooks import WebhookConfig
from general_ludd.security.auth import AuthPosture
from general_ludd.security.sts import StsToken

_SECRET = "SUPER-SECRET-SENTINEL-VALUE"  # pragma: allowlist secret


def test_auth_posture_psk_absent_from_repr() -> None:
    posture = AuthPosture(
        psk=_SECRET,
        require_auth=True,
        no_auth=False,
        surface="worker",
    )
    assert _SECRET not in repr(posture)
    assert _SECRET not in str(posture)
    # Non-secret fields are still present (repr remains useful for debugging).
    assert "worker" in repr(posture)
    # The value is still readable via the attribute — only the repr is redacted.
    assert posture.psk == _SECRET


def test_sts_token_token_id_absent_from_repr() -> None:
    token = StsToken(
        token_id=_SECRET,
        issuer_agent_id="issuer",
        subject_agent_id="subject",
        spec="<spec-placeholder>",  # dataclass does not validate the type
        issued_at=0.0,
        expires_at=3600.0,
    )
    assert _SECRET not in repr(token)
    assert _SECRET not in str(token)
    assert "issuer" in repr(token)
    assert token.token_id == _SECRET


def test_webhook_config_headers_absent_from_repr() -> None:
    config = WebhookConfig(
        url="https://example.test/hook",
        headers={"Authorization": f"Bearer {_SECRET}"},
    )
    assert _SECRET not in repr(config)
    assert _SECRET not in str(config)
    assert "https://example.test/hook" in repr(config)
    assert config.headers["Authorization"] == f"Bearer {_SECRET}"
