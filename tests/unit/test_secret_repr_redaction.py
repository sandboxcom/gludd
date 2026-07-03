"""Secrets must never leak through a dataclass/model ``repr`` into logs/tracebacks.

Round 1 (frozen/plain dataclasses carrying bearer-grade secrets):

  * ``AuthPosture.psk``       — the raw pre-shared key.
  * ``StsToken.token_id``     — the opaque bearer token.
  * ``WebhookConfig.headers`` — outbound auth headers.

Round 2 (five more secret-bearing fields across dataclasses + a pydantic model):

  * ``SelfUpdateRequest.approval_token`` — bearer approval token (frozen dc).
  * ``SandboxHandle.token``              — backend-specific sandbox token (dc).
  * ``RegisterHookRequest.headers``      — outbound hook auth headers (pydantic).
  * ``connectors.podman.Response.headers``        — upstream HTTP response headers.
  * ``connectors.docker_engine.Response.headers`` — upstream HTTP response headers.

Each of those fields is marked ``field(repr=False)`` (or pydantic
``Field(..., repr=False)``) so the value is omitted from the default ``repr``
(and therefore from f-string interpolation, logging, and exception rendering).
These tests pin that contract: the secret sentinel must be ABSENT from
``repr()``/``str()`` while remaining readable via the attribute.
"""

from __future__ import annotations

from general_ludd.connectors.docker_engine import _DockerResponse as DockerResponse
from general_ludd.connectors.podman import _PodmanResponse as PodmanResponse
from general_ludd.events.hooks import WebhookConfig
from general_ludd.routers.reload import RegisterHookRequest
from general_ludd.security.auth import AuthPosture
from general_ludd.security.sandboxes import SandboxHandle
from general_ludd.security.sts import StsToken
from general_ludd.self_update.model import SelfUpdateRequest

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


# --------------------------------------------------------------------------- #
# Round 2
# --------------------------------------------------------------------------- #
def test_self_update_request_approval_token_absent_from_repr() -> None:
    req = SelfUpdateRequest(
        raw_text="update gludd: bump the retry cap",
        requested_by="operator-1",
        approval_token=_SECRET,
    )
    assert _SECRET not in repr(req)
    assert _SECRET not in str(req)
    # Non-secret fields remain in the repr for debuggability.
    assert "operator-1" in repr(req)
    # Value still readable via the attribute — only the repr is redacted.
    assert req.approval_token == _SECRET


def test_sandbox_handle_token_absent_from_repr() -> None:
    handle = SandboxHandle(backend="apparmor", token=_SECRET)
    assert _SECRET not in repr(handle)
    assert _SECRET not in str(handle)
    assert "apparmor" in repr(handle)
    assert handle.token == _SECRET


def test_register_hook_request_headers_absent_from_repr() -> None:
    req = RegisterHookRequest(
        event_name="config.reloaded",
        url="https://example.test/hook",
        headers={"X-Api-Key": _SECRET},
    )
    assert _SECRET not in repr(req)
    assert _SECRET not in str(req)
    assert "config.reloaded" in repr(req)
    assert req.headers is not None
    assert req.headers["X-Api-Key"] == _SECRET


def test_podman_response_headers_absent_from_repr() -> None:
    resp = PodmanResponse(
        status=200,
        headers={"X-Api-Key": _SECRET},
        body=b"",
    )
    assert _SECRET not in repr(resp)
    assert _SECRET not in str(resp)
    assert "200" in repr(resp)
    assert resp.headers["X-Api-Key"] == _SECRET


def test_docker_engine_response_headers_absent_from_repr() -> None:
    resp = DockerResponse(
        status=200,
        headers={"X-Api-Key": _SECRET},
        body=b"",
    )
    assert _SECRET not in repr(resp)
    assert _SECRET not in str(resp)
    assert "200" in repr(resp)
    assert resp.headers["X-Api-Key"] == _SECRET
