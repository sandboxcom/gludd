"""Unit tests for StsIssuer / StsToken.

TDD: written BEFORE ``StsIssuer``/``StsToken`` exist in
``src/general_ludd/security/sts.py``. Imports will fail until those classes
are added.
"""

from __future__ import annotations

import time

import pytest

from general_ludd.security.permissions import (
    Capability,
    PermissionDeniedError,
    PermissionSpec,
)
from general_ludd.security.sts import StsIssuer, StsToken


def _issuer_spec(max_ttl: int = 3600) -> PermissionSpec:
    return PermissionSpec(
        agent_type="primary",
        capabilities=[
            Capability(
                resource="file:tmp",
                actions=["read", "write"],
                constraints={"path_prefix": "/tmp/gludd/"},
            )
        ],
        max_sts_ttl_seconds=max_ttl,
    )


def _subject_spec(actions: list[str] | None = None) -> PermissionSpec:
    return PermissionSpec(
        agent_type="subagent",
        capabilities=[
            Capability(
                resource="file:tmp",
                actions=actions if actions is not None else ["read"],
                constraints={"path_prefix": "/tmp/gludd/sub/"},
            )
        ],
    )


def test_issue_creates_token_with_uuid() -> None:
    issuer = StsIssuer()
    token = issuer.issue(
        issuer_spec=_issuer_spec(),
        subject_spec_request=_subject_spec(),
        issuer_id="primary-1",
        subject_id="sub-1",
        ttl_seconds=60,
    )
    assert isinstance(token, StsToken)
    assert isinstance(token.token_id, str) and len(token.token_id) > 0
    assert token.issuer_agent_id == "primary-1"
    assert token.subject_agent_id == "sub-1"
    assert token.issued_at < token.expires_at
    assert token.use_count == 0
    assert token.last_used_at is None


def test_validate_expired_token_fails() -> None:
    issuer = StsIssuer()
    token = issuer.issue(
        issuer_spec=_issuer_spec(),
        subject_spec_request=_subject_spec(),
        issuer_id="primary-1",
        subject_id="sub-1",
        ttl_seconds=0,
    )
    required = Capability(
        resource="file:tmp",
        actions=["read"],
        constraints={"path_prefix": "/tmp/gludd/sub/"},
    )
    time.sleep(0.01)
    assert issuer.validate(token, required) is False


def test_record_use_bumps_counters() -> None:
    issuer = StsIssuer()
    token = issuer.issue(
        issuer_spec=_issuer_spec(),
        subject_spec_request=_subject_spec(),
        issuer_id="primary-1",
        subject_id="sub-1",
        ttl_seconds=60,
    )
    assert token.use_count == 0
    issuer.record_use(token.token_id)
    refreshed = issuer.get_token(token.token_id)
    assert refreshed is not None
    assert refreshed.use_count == 1
    assert refreshed.last_used_at is not None


def test_issue_rejects_overprivileged_request() -> None:
    issuer = StsIssuer()
    with pytest.raises(PermissionDeniedError):
        issuer.issue(
            issuer_spec=_issuer_spec(),
            subject_spec_request=_subject_spec(actions=["read", "delete"]),
            issuer_id="primary-1",
            subject_id="sub-1",
            ttl_seconds=60,
        )


def test_issue_caps_ttl_at_issuer_max() -> None:
    issuer = StsIssuer()
    token = issuer.issue(
        issuer_spec=_issuer_spec(max_ttl=300),
        subject_spec_request=_subject_spec(),
        issuer_id="primary-1",
        subject_id="sub-1",
        ttl_seconds=99999,
    )
    assert (token.expires_at - token.issued_at) <= 300


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
