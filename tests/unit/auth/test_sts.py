"""Unit tests for STS token contracts (general_ludd.auth.sts)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.auth.sts import (
    _TERMINAL_STATES,
    TokenGrant,
    TokenRequest,
    TokenRevocation,
    TokenValidation,
)


class TestTokenRequest:
    def test_minimal_request(self) -> None:
        req = TokenRequest(agent_id="agent-1", parent_agent_id="agent-0")
        assert req.agent_id == "agent-1"
        assert req.parent_agent_id == "agent-0"
        assert req.project_id is None
        assert req.scope_actions is None
        assert req.parent_role == "admin"
        assert req.ttl_seconds == 3600
        assert req.request_id is None

    def test_full_request(self) -> None:
        req = TokenRequest(
            agent_id="agent-2",
            parent_agent_id="agent-0",
            project_id="proj-a",
            scope_actions=("read", "execute"),
            parent_role="operator",
            ttl_seconds=7200,
            request_id="idem-123",
        )
        assert req.agent_id == "agent-2"
        assert req.parent_agent_id == "agent-0"
        assert req.project_id == "proj-a"
        assert req.scope_actions == ("read", "execute")
        assert req.parent_role == "operator"
        assert req.ttl_seconds == 7200
        assert req.request_id == "idem-123"

    def test_agent_id_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="agent_id"):
            TokenRequest(agent_id="", parent_agent_id="agent-0")

    def test_agent_id_rejects_whitespace(self) -> None:
        with pytest.raises(ValueError, match="agent_id"):
            TokenRequest(agent_id="  ", parent_agent_id="agent-0")

    def test_parent_agent_id_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="parent_agent_id"):
            TokenRequest(agent_id="agent-1", parent_agent_id="")

    def test_ttl_seconds_rejects_zero(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            TokenRequest(agent_id="a", parent_agent_id="p", ttl_seconds=0)

    def test_ttl_seconds_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            TokenRequest(agent_id="a", parent_agent_id="p", ttl_seconds=-1)

    def test_frozen(self) -> None:
        req = TokenRequest(agent_id="a", parent_agent_id="p")
        with pytest.raises(FrozenInstanceError):
            req.agent_id = "b"  # type: ignore[misc]

    def test_asdict(self) -> None:
        req = TokenRequest(agent_id="a", parent_agent_id="p", ttl_seconds=1800)
        d = asdict(req)
        assert d["agent_id"] == "a"
        assert d["parent_agent_id"] == "p"
        assert d["ttl_seconds"] == 1800

    def test_equality(self) -> None:
        a = TokenRequest(agent_id="x", parent_agent_id="y")
        b = TokenRequest(agent_id="x", parent_agent_id="y")
        assert a == b
        assert hash(a) == hash(b)


class TestTokenGrant:
    def _now(self) -> datetime:
        return datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)

    def _expiry(self) -> datetime:
        return self._now() + timedelta(hours=1)

    def test_full_grant(self) -> None:
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="agent-1",
            parent_agent_id="agent-0",
            role_id="rid-xxx",
            secret_id="s3cret!",
            role_name="agent-agent-1",
            scope_hash="abc123",
            scope_actions=("read", "execute"),
            created_at=self._now(),
            expires_at=self._expiry(),
        )
        assert g.token_id == "tok-abc"
        assert g.role_id == "rid-xxx"
        assert g.scope_actions == ("read", "execute")
        assert g.project_id is None
        assert g.hydration_count == 0

    def test_grant_with_project(self) -> None:
        g = TokenGrant(
            token_id="tok-xyz",
            agent_id="agent-2",
            parent_agent_id="agent-0",
            role_id="rid-yyy",
            secret_id="s3cret!",
            role_name="agent-agent-2",
            scope_hash="def456",
            scope_actions=(),
            created_at=self._now(),
            expires_at=self._expiry(),
            project_id="proj-p",
        )
        assert g.project_id == "proj-p"

    def test_secret_id_masked_in_repr(self) -> None:
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="super-secret-value-12345",
            role_name="rn",
            scope_hash="h",
            scope_actions=(),
            created_at=self._now(),
            expires_at=self._expiry(),
        )
        r = repr(g)
        assert "super-secret-value-12345" not in r
        assert "***" in r

    def test_secret_id_unmasked_on_instance(self) -> None:
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="my-secret",
            role_name="rn",
            scope_hash="h",
            scope_actions=(),
            created_at=self._now(),
            expires_at=self._expiry(),
        )
        assert g.secret_id == "my-secret"

    def test_frozen(self) -> None:
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="h",
            scope_actions=(),
            created_at=self._now(),
            expires_at=self._expiry(),
        )
        with pytest.raises(FrozenInstanceError):
            g.token_id = "new"  # type: ignore[misc]

    def test_is_expired(self) -> None:
        past = self._now() - timedelta(hours=2)
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="h",
            scope_actions=(),
            created_at=past - timedelta(hours=1),
            expires_at=past,
        )
        assert g.is_expired(now=self._now()) is True

    def test_is_not_expired(self) -> None:
        future = self._now() + timedelta(hours=2)
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="h",
            scope_actions=(),
            created_at=self._now(),
            expires_at=future,
        )
        assert g.is_expired(now=self._now()) is False

    def test_remaining_seconds(self) -> None:
        future = self._now() + timedelta(seconds=120)
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="h",
            scope_actions=(),
            created_at=self._now(),
            expires_at=future,
        )
        remaining = g.remaining_seconds(now=self._now())
        assert 110 <= remaining <= 120

    def test_remaining_seconds_expired(self) -> None:
        past = self._now() - timedelta(seconds=10)
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="h",
            scope_actions=(),
            created_at=self._now() - timedelta(hours=2),
            expires_at=past,
        )
        assert g.remaining_seconds(now=self._now()) == 0

    def test_asdict(self) -> None:
        g = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="h",
            scope_actions=("read",),
            created_at=self._now(),
            expires_at=self._expiry(),
            project_id="proj-x",
        )
        d = asdict(g)
        assert d["token_id"] == "tok-abc"
        assert d["project_id"] == "proj-x"
        assert d["scope_actions"] == ("read",)
        assert d["hydration_count"] == 0


class TestTokenValidation:
    def test_valid_active(self) -> None:
        v = TokenValidation(
            token_id="tok-abc",
            valid=True,
            status="active",
            scope_hash_match=True,
            remaining_seconds=1800,
        )
        assert v.valid is True
        assert v.status == "active"
        assert v.reason is None

    def test_invalid_expired(self) -> None:
        v = TokenValidation(
            token_id="tok-abc",
            valid=False,
            status="expired",
            scope_hash_match=False,
            remaining_seconds=0,
            reason="TTL exceeded",
        )
        assert v.valid is False
        assert v.status == "expired"
        assert v.reason == "TTL exceeded"
        assert v.remaining_seconds == 0

    def test_valid_with_none_remaining(self) -> None:
        v = TokenValidation(
            token_id="tok-abc",
            valid=True,
            status="active",
            scope_hash_match=True,
        )
        assert v.remaining_seconds is None

    def test_unknown_token(self) -> None:
        v = TokenValidation(
            token_id="tok-unk",
            valid=False,
            status="unknown",
            scope_hash_match=False,
            reason="Token not found",
        )
        assert v.valid is False
        assert v.status == "unknown"

    def test_revoked(self) -> None:
        v = TokenValidation(
            token_id="tok-abc",
            valid=False,
            status="revoked",
            scope_hash_match=False,
            reason="Token revoked at 2026-08-02T12:00:00Z",
        )
        assert v.status == "revoked"

    def test_frozen(self) -> None:
        v = TokenValidation(token_id="tok-abc", valid=True, status="active", scope_hash_match=True)
        with pytest.raises(FrozenInstanceError):
            v.valid = False  # type: ignore[misc]

    def test_asdict(self) -> None:
        v = TokenValidation(
            token_id="tok-abc",
            valid=False,
            status="revoked",
            scope_hash_match=False,
            remaining_seconds=0,
            reason="revoked",
        )
        d = asdict(v)
        assert d["token_id"] == "tok-abc"
        assert d["valid"] is False
        assert d["status"] == "revoked"
        assert d["reason"] == "revoked"

    def test_equality(self) -> None:
        a = TokenValidation(token_id="t", valid=True, status="active", scope_hash_match=True)
        b = TokenValidation(token_id="t", valid=True, status="active", scope_hash_match=True)
        assert a == b


class TestTokenRevocation:
    def _now(self) -> datetime:
        return datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)

    def test_basic_revocation(self) -> None:
        r = TokenRevocation(
            token_id="tok-abc",
            agent_id="agent-1",
            parent_agent_id="agent-0",
            revoked_at=self._now(),
            terminal_state="completed",
        )
        assert r.token_id == "tok-abc"
        assert r.agent_id == "agent-1"
        assert r.terminal_state == "completed"
        assert r.cascade is False
        assert r.reason is None

    def test_cascade_revocation(self) -> None:
        r = TokenRevocation(
            token_id="tok-abc",
            agent_id="agent-1",
            parent_agent_id="agent-0",
            revoked_at=self._now(),
            terminal_state="cascade",
            cascade=True,
            reason="Parent agent completed",
        )
        assert r.cascade is True
        assert r.terminal_state == "cascade"
        assert r.reason == "Parent agent completed"

    def test_terminal_state_rejects_invalid(self) -> None:
        with pytest.raises(ValueError, match="terminal_state"):
            TokenRevocation(
                token_id="tok-abc",
                agent_id="agent-1",
                parent_agent_id="agent-0",
                revoked_at=self._now(),
                terminal_state="bogus",
            )

    def test_all_valid_terminal_states(self) -> None:
        now = self._now()
        for state in sorted(_TERMINAL_STATES):
            r = TokenRevocation(
                token_id="tok-abc",
                agent_id="agent-1",
                parent_agent_id="agent-0",
                revoked_at=now,
                terminal_state=state,
            )
            assert r.terminal_state == state

    def test_frozen(self) -> None:
        r = TokenRevocation(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            revoked_at=self._now(),
            terminal_state="completed",
        )
        with pytest.raises(FrozenInstanceError):
            r.terminal_state = "cancelled"  # type: ignore[misc]

    def test_asdict(self) -> None:
        r = TokenRevocation(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            revoked_at=self._now(),
            terminal_state="cancelled",
            cascade=True,
            reason="timeout",
        )
        d = asdict(r)
        assert d["token_id"] == "tok-abc"
        assert d["terminal_state"] == "cancelled"
        assert d["cascade"] is True
        assert d["reason"] == "timeout"

    def test_equality(self) -> None:
        now = self._now()
        a = TokenRevocation(token_id="t", agent_id="a", parent_agent_id="p", revoked_at=now, terminal_state="completed")
        b = TokenRevocation(token_id="t", agent_id="a", parent_agent_id="p", revoked_at=now, terminal_state="completed")
        assert a == b


class TestTokenRequestValidationEnvelope:
    def test_build_active_validation(self) -> None:
        now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
        future = now + timedelta(hours=1)
        grant = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="abc123",
            scope_actions=("read",),
            created_at=now,
            expires_at=future,
        )
        v = TokenValidation.from_grant(grant, expected_scope_hash="abc123", now=now)
        assert v.valid is True
        assert v.status == "active"
        assert v.scope_hash_match is True
        assert v.reason is None

    def test_build_expired_validation(self) -> None:
        now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
        past = now - timedelta(hours=1)
        grant = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="abc123",
            scope_actions=("read",),
            created_at=past - timedelta(hours=1),
            expires_at=past,
        )
        v = TokenValidation.from_grant(grant, expected_scope_hash="abc123", now=now)
        assert v.valid is False
        assert v.status == "expired"
        assert v.remaining_seconds == 0

    def test_build_scope_mismatch(self) -> None:
        now = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
        future = now + timedelta(hours=1)
        grant = TokenGrant(
            token_id="tok-abc",
            agent_id="a",
            parent_agent_id="p",
            role_id="r",
            secret_id="s",
            role_name="rn",
            scope_hash="abc123",
            scope_actions=("read",),
            created_at=now,
            expires_at=future,
        )
        v = TokenValidation.from_grant(grant, expected_scope_hash="xyz789", now=now)
        assert v.valid is False
        assert v.status == "active"
        assert v.scope_hash_match is False
        assert "scope hash" in (v.reason or "").lower()

    def test_build_unknown_validation(self) -> None:
        v = TokenValidation.from_grant(None, expected_scope_hash="any")
        assert v.valid is False
        assert v.status == "unknown"
        assert v.scope_hash_match is False
        assert v.token_id == "unknown"


class TestContractsImportable:
    def test_module_exports(self) -> None:
        from general_ludd.auth.sts import (
            _TERMINAL_STATES,
            TokenGrant,
            TokenRequest,
            TokenRevocation,
            TokenValidation,
        )

        for cls in (TokenRequest, TokenGrant, TokenValidation, TokenRevocation):
            assert cls is not None
        assert isinstance(_TERMINAL_STATES, frozenset)
        assert len(_TERMINAL_STATES) >= 5
