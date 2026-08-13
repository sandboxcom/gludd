"""E2E tests for the full STS token lifecycle.

Exercises the complete lifecycle — mint, use, expire, revive, revoke —
with real PermissionSpec intersection, capability narrowing, audit event
recording, and post-revocation rejection. Uses StsIssuer for delegation,
CapabilityNarrowing for scope intersection, and StsAuditLog for audit
trace. Avoids duplicating unit-shape assertions; this file only tests
end-to-end behavioral flows.

For ``secret:openbao`` resource tests, the ``openbao_paths`` constraint
is compared as a literal set-subset check (glob patterns are not expanded),
so child paths must be a literal member of the parent's path set. For
``file:tmp`` resource tests, segment-based path-prefix narrowing is used
(``/tmp/gludd/sub/`` is narrower than ``/tmp/gludd/``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    ToolAction,
)
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)
from general_ludd.security.sts import (
    StsAuditLog,
    StsIssuer,
    StsToken,
)
from general_ludd.sts.audit import StsAuditPipeline
from general_ludd.sts.narrowing import CapabilityNarrowing, OpenBaoPolicyRenderer
from general_ludd.sts.reviver import TokenRevivalError, TokenReviver
from general_ludd.sts.revoker import TokenRevoker

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class FixedClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# file:tmp specs — used by StsIssuer tests where segment-based
# path-prefix narrowing works properly (as in test_e2e_security_sts.py).
def _issuer_spec(actions: list[str] | None = None, max_ttl: int = 3600) -> PermissionSpec:
    return PermissionSpec(
        agent_type="primary",
        capabilities=[
            Capability(
                resource="file:tmp",
                actions=actions or ["read", "write"],
                constraints={"path_prefix": "/tmp/gludd/"},
            ),
        ],
        max_sts_ttl_seconds=max_ttl,
    )


def _subject_spec(actions: list[str] | None = None) -> PermissionSpec:
    return PermissionSpec(
        agent_type="subagent",
        capabilities=[
            Capability(
                resource="file:tmp",
                actions=actions or ["read"],
                constraints={"path_prefix": "/tmp/gludd/sub/"},
            ),
        ],
    )


# secret:openbao specs — child paths must be a literal member of the
# parent's path set (glob patterns are not expanded).
def _openbao_issuer_spec(actions: list[str] | None = None, paths: list[str] | None = None) -> PermissionSpec:
    return PermissionSpec(
        agent_type="primary",
        capabilities=[
            Capability(
                resource="secret:openbao",
                actions=actions or ["read", "write"],
                constraints={"openbao_paths": paths or ["secret/data/gludd/*"]},
            ),
        ],
        max_sts_ttl_seconds=3600,
    )


def _openbao_subject_spec(actions: list[str] | None = None, paths: list[str] | None = None) -> PermissionSpec:
    return PermissionSpec(
        agent_type="subagent",
        capabilities=[
            Capability(
                resource="secret:openbao",
                actions=actions or ["read"],
                constraints={"openbao_paths": paths or ["secret/data/gludd/*"]},
            ),
        ],
    )


# ------------------------------------------------------------------
# Full lifecycle: mint → use → expire → (revive rejected) → revoke
# ------------------------------------------------------------------


class TestFullTokenLifecycle:
    """End-to-end lifecycle: mint, use, expiry, revival reject, revocation.

    The lifecycle phases:
      phase-0: issuer mints a token → active, usable
      phase-1: validate(token, read-cap) → True (token is live)
      phase-2: clock advance past TTL → token expired, validate → False
      phase-3: revive a revoked token → TokenRevivalError
      phase-4: revoke(token) → True, get_token → None, validate → False
    """

    def test_full_lifecycle_mint_use_expire_revoke(self):
        clock = FixedClock(1000.0)
        issuer = StsIssuer(clock=clock)
        audit_log = StsAuditLog()

        # ── phase-0a: mint ──
        token = issuer.issue(
            issuer_spec=_issuer_spec(max_ttl=600),
            subject_spec_request=_subject_spec(actions=["read"]),
            issuer_id="primary-1",
            subject_id="sub-lifecycle-1",
            ttl_seconds=30,
        )
        audit_log.record_issue(token)

        assert isinstance(token, StsToken)
        assert token.issuer_agent_id == "primary-1"
        assert token.subject_agent_id == "sub-lifecycle-1"
        assert token.expires_at > clock.now

        events = audit_log.query(agent_id="sub-lifecycle-1")
        assert len(events) == 1
        assert events[0]["event"] == "issued"

        # ── phase-0b: use / validate ──
        read_cap = Capability(
            resource="file:tmp",
            actions=["read"],
            constraints={"path_prefix": "/tmp/gludd/sub/"},
        )
        assert issuer.validate(token, read_cap) is True

        audit_log.record_use(token.token_id, read_cap, "/tmp/gludd/sub/output.txt")
        events = audit_log.query(agent_id="sub-lifecycle-1")
        event_types = {e["event"] for e in events}
        assert "used" in event_types

        # ── phase-1: expiry ──
        clock.advance(31)
        assert issuer.validate(token, read_cap) is False
        audit_log.record_expiry(token.token_id)

        events = audit_log.query(agent_id="sub-lifecycle-1")
        event_types = {e["event"] for e in events}
        assert "expired" in event_types

        # ── phase-2: post-expiry get_token returns None ──
        assert issuer.get_token(token.token_id) is None

        # ── phase-3: revoke (already expired, but covered) ──
        revoked = issuer.revoke(token.token_id)
        assert revoked is True
        assert issuer.get_token(token.token_id) is None
        assert issuer.validate(token, read_cap) is False

    def test_audit_events_for_each_lifecycle_phase(self):
        """Every lifecycle transition produces the correct audit event."""
        audit_log = StsAuditLog()
        issuer = StsIssuer()

        token = issuer.issue(
            issuer_spec=_issuer_spec(),
            subject_spec_request=_subject_spec(),
            issuer_id="primary-audit",
            subject_id="sub-audit",
            ttl_seconds=300,
        )
        audit_log.record_issue(token)
        assert len(audit_log.query(agent_id="sub-audit")) == 1

        cap = Capability(
            resource="file:tmp",
            actions=["read"],
            constraints={"path_prefix": "/tmp/gludd/sub/"},
        )
        audit_log.record_use(token.token_id, cap, "/tmp/gludd/sub/cosign")
        assert len(audit_log.query(agent_id="sub-audit")) == 2

        audit_log.record_expiry(token.token_id)
        events = audit_log.query(agent_id="sub-audit")
        assert len(events) == 3

        assert "issued" in {e["event"] for e in events}
        assert "used" in {e["event"] for e in events}
        assert "expired" in {e["event"] for e in events}


# ------------------------------------------------------------------
# Capability narrowing at dispatch time
# ------------------------------------------------------------------


class TestCapabilityNarrowingAtDispatch:
    """When a parent dispatches a subagent, the child's scope must be narrowed
    via CapabilityNarrowing before the token is minted.

    Per spec (FEATURE_STS_TOKENS.md §2):
        child_actions = parent_actions ∩ child_native_actions
    """

    def test_child_scope_is_always_subset_of_parent(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)

        parent_actions = {
            ToolAction.READ,
            ToolAction.WRITE,
            ToolAction.EXECUTE,
            ToolAction.DELETE,
        }
        narrowed = narrowing.narrow(parent_actions, parent_role="admin")

        parent_all = lattice.all_actions("admin")
        assert narrowed.issubset(parent_all)
        assert ToolAction.READ.value in narrowed

    def test_child_cannot_gain_actions_parent_lacks(self):
        """A reader parent cannot grant write/delete to a child."""
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)

        child_actions = {ToolAction.WRITE}
        narrowed = narrowing.narrow(child_actions, parent_role="reader")

        assert ToolAction.WRITE.value not in narrowed

    def test_narrowed_policy_renders_as_valid_hcl(self):
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)
        actions = {ToolAction.READ, ToolAction.WRITE}

        hcl = narrowing.to_openbao_policy(actions, role_name="subagent-build")
        assert isinstance(hcl, str)
        assert len(hcl) > 0
        assert "subagent-build" in hcl
        assert "capabilities" in hcl

    def test_empty_narrowing_produces_empty_policy(self):
        """An empty intersection yields an empty rendered policy string."""
        lattice = CapabilityLattice()
        narrowing = CapabilityNarrowing(lattice)

        narrowed = narrowing.narrow({ToolAction.DELETE}, parent_role="reader")
        assert narrowed == set()

        hcl = OpenBaoPolicyRenderer.render(narrowed)
        assert hcl == ""

    def test_sts_issuer_narrows_actions_for_openbao_resource(self):
        """StsIssuer validates that child actions are subset of parent actions
        for an openbao resource when paths are a literal subset."""
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=_openbao_issuer_spec(
                actions=["read", "write"],
                paths=["secret/data/gludd/*", "secret/data/gludd/build/*"],
            ),
            subject_spec_request=_openbao_subject_spec(
                actions=["read"],
                paths=["secret/data/gludd/build/*"],
            ),
            issuer_id="primary-1",
            subject_id="sub-obao-1",
            ttl_seconds=300,
        )

        assert isinstance(token, StsToken)
        read_cap = Capability(
            resource="secret:openbao",
            actions=["read"],
            constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
        )
        assert issuer.validate(token, read_cap) is True

        write_cap = Capability(
            resource="secret:openbao",
            actions=["write"],
            constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
        )
        assert issuer.validate(token, write_cap) is False

    def test_permission_spec_intersection_on_openbao_resource(self):
        """PermissionSpecParser.intersection produces the tightest binding
        for secret:openbao when paths overlap as a literal set intersection."""
        agent_spec = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write"],
                    constraints={"openbao_paths": ["secret/data/gludd/*"]},
                ),
            ],
        )
        child_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/gludd/*"]},
                ),
            ],
        )

        inter = PermissionSpecParser.intersection(agent_spec, child_spec)
        assert inter.subject == PermissionSubject.STS_TOKEN

        secret_cap = next(
            (c for c in inter.capabilities if c.resource == "secret:openbao"), None
        )
        assert secret_cap is not None
        assert set(secret_cap.actions) == {"read"}
        assert "secret/data/gludd/*" in secret_cap.constraints.get("openbao_paths", [])


# ------------------------------------------------------------------
# Token does not survive restart without revival
# ------------------------------------------------------------------


class TestTokenSurvivalAcrossRestart:
    """An STS token does NOT survive a subagent restart — TokenReviver
    must mint a fresh secret_id for the same AppRole (same role_name).
    """

    @pytest.mark.asyncio
    async def test_revive_mints_fresh_secret_id_for_existing_role(self):
        mock_secrets = MagicMock()
        mock_secrets.rotate_approle_secret_id.return_value = "fresh-after-restart"

        mock_record = MagicMock()
        mock_record.role_name = "agent-sub-99"
        mock_record.role_id = "role-sub-99"
        mock_record.token_id = "tok-sub-99"
        mock_record.revoked_at = None
        mock_record.parent_agent_id = "primary-1"

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record
        mock_store.increment_hydration.return_value = None

        reviver = TokenReviver(mock_secrets, mock_store)
        creds = await reviver.revive("sub-99")

        mock_store.get.assert_called_once_with("sub-99")
        mock_secrets.rotate_approle_secret_id.assert_called_once_with(
            "agent-sub-99"
        )
        mock_store.increment_hydration.assert_called_once_with("sub-99")

        assert creds.role_id == "role-sub-99"
        assert creds.secret_id == "fresh-after-restart"

    @pytest.mark.asyncio
    async def test_revive_revoked_token_raises_error(self):
        """A revoked token cannot be revived — TokenRevivalError."""
        from datetime import UTC, datetime

        mock_secrets = MagicMock()
        mock_record = MagicMock()
        mock_record.role_name = "agent-revoked-1"
        mock_record.role_id = "role-revoked-1"
        mock_record.token_id = "tok-revoked-1"
        mock_record.revoked_at = datetime(2026, 7, 14, tzinfo=UTC)
        mock_record.parent_agent_id = "primary-1"

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record

        reviver = TokenReviver(mock_secrets, mock_store)

        with pytest.raises(TokenRevivalError, match="revoked"):
            await reviver.revive("revoked-1")

    @pytest.mark.asyncio
    async def test_revive_nonexistent_role_raises_error(self):
        mock_secrets = MagicMock()
        mock_store = AsyncMock()
        mock_store.get.return_value = None

        reviver = TokenReviver(mock_secrets, mock_store)

        with pytest.raises(TokenRevivalError, match="No token record"):
            await reviver.revive("ghost")

    @pytest.mark.asyncio
    async def test_revoker_marks_store_revoked_and_destroys_approle(self):
        """TokenRevoker destroys AppRole and marks record revoked."""
        mock_client = MagicMock()
        mock_client.auth.approle.delete_role.return_value = None

        mock_secrets = MagicMock()
        mock_secrets._client = mock_client

        mock_record = MagicMock()
        mock_record.token_id = "tok-revoker-e2e"
        mock_record.role_name = "agent-sub-revoker"
        mock_record.role_id = "role-revoker"
        mock_record.revoked_at = None
        mock_record.parent_agent_id = "primary-1"

        mock_store = AsyncMock()
        mock_store.get.return_value = mock_record
        mock_store.revoke.return_value = None

        revoker = TokenRevoker(mock_secrets, mock_store)
        await revoker.revoke("sub-revoker")

        mock_client.auth.approle.delete_role.assert_called_once_with(
            "agent-sub-revoker"
        )
        mock_store.revoke.assert_called_once_with("tok-revoker-e2e")


# ------------------------------------------------------------------
# StsAuditPipeline records events for each lifecycle phase
# ------------------------------------------------------------------


class TestAuditPipelineLifecycleEvents:
    """StsAuditPipeline records mint, use, renew, revoke, revive events
    against the correct token_id.
    """

    @pytest.mark.asyncio
    async def test_full_audit_cycle_mint_use_revoke(self):
        import json as _json

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0
        mock_row.last_used_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)

        await pipeline.record_mint(
            token_id="tok-full",
            issuer_agent_id="primary-1",
            subject_agent_id="sub-1",
            scope_actions=["read", "write"],
        )
        await pipeline.record_use(
            token_id="tok-full",
            agent_id="sub-1",
            parent_agent_id="primary-1",
        )
        await pipeline.record_revoke(
            token_id="tok-full",
            agent_id="sub-1",
            parent_agent_id="primary-1",
        )

        events = _json.loads(mock_row.events)
        assert len(events) == 3
        assert events[0]["action"] == "mint"
        assert events[1]["action"] == "use"
        assert events[2]["action"] == "revoke"
        assert mock_row.use_count == 3
        assert isinstance(events[0]["timestamp"], float)
        assert isinstance(events[0]["scope_hash"], str)

    @pytest.mark.asyncio
    async def test_revive_event_recorded(self):
        import json as _json

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session_factory = MagicMock()
        mock_session_factory.begin.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.events = "[]"
        mock_row.use_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        pipeline = StsAuditPipeline(mock_session_factory)

        await pipeline.record_revive(
            token_id="tok-revive",
            agent_id="sub-1",
            parent_agent_id="primary-1",
        )

        events = _json.loads(mock_row.events)
        assert len(events) == 1
        assert events[0]["action"] == "revive"
        assert events[0]["agent_id"] == "sub-1"
        assert events[0]["parent_agent_id"] == "primary-1"


# ------------------------------------------------------------------
# C-SEC-1: denial union propagation
# ------------------------------------------------------------------


class TestDenialUnionPropagation:
    """A denial on the issuer propagates into the child token spec,
    and the child's own denials are additive (union, never dropped)."""

    def test_issuer_denial_propagates_to_child(self):
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=PermissionSpec(
                agent_type="primary",
                capabilities=[
                    Capability(
                        resource="file:tmp",
                        actions=["read", "write"],
                        constraints={"path_prefix": "/tmp/gludd/"},
                    ),
                ],
                denied=[
                    Capability(
                        resource="file:tmp",
                        actions=["delete"],
                        constraints={},
                    ),
                ],
            ),
            subject_spec_request=PermissionSpec(
                agent_type="subagent",
                capabilities=[
                    Capability(
                        resource="file:tmp",
                        actions=["read", "write"],
                        constraints={"path_prefix": "/tmp/gludd/sub/"},
                    ),
                ],
            ),
            issuer_id="primary-1",
            subject_id="sub-denial-1",
            ttl_seconds=300,
        )

        denied_actions: set[str] = set()
        for d in token.spec.denied:
            denied_actions.update(d.actions)
        assert "delete" in denied_actions

    def test_child_and_issuer_denials_are_union(self):
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=PermissionSpec(
                agent_type="primary",
                capabilities=[
                    Capability(
                        resource="file:tmp",
                        actions=["read", "write", "delete", "execute"],
                        constraints={"path_prefix": "/tmp/gludd/"},
                    ),
                ],
                denied=[
                    Capability(
                        resource="file:tmp",
                        actions=["delete"],
                        constraints={},
                    ),
                ],
            ),
            subject_spec_request=PermissionSpec(
                agent_type="subagent",
                capabilities=[
                    Capability(
                        resource="file:tmp",
                        actions=["read", "write"],
                        constraints={"path_prefix": "/tmp/gludd/sub/"},
                    ),
                ],
                denied=[
                    Capability(
                        resource="file:tmp",
                        actions=["write"],
                        constraints={},
                    ),
                ],
            ),
            issuer_id="primary-1",
            subject_id="sub-union-1",
            ttl_seconds=300,
        )

        denied_actions: set[str] = set()
        for d in token.spec.denied:
            denied_actions.update(d.actions)
        assert "delete" in denied_actions
        assert "write" in denied_actions

    def test_openbao_denial_propagates_with_matching_paths(self):
        """Issuer denial for secret:openbao propagates when child's paths
        are a literal subset of issuer's paths."""
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=PermissionSpec(
                agent_type="primary",
                capabilities=[
                    Capability(
                        resource="secret:openbao",
                        actions=["read", "write"],
                        constraints={"openbao_paths": ["secret/data/gludd/*"]},
                    ),
                ],
                denied=[
                    Capability(
                        resource="secret:openbao",
                        actions=["delete"],
                        constraints={},
                    ),
                ],
            ),
            subject_spec_request=PermissionSpec(
                agent_type="subagent",
                capabilities=[
                    Capability(
                        resource="secret:openbao",
                        actions=["read", "write"],
                        constraints={"openbao_paths": ["secret/data/gludd/*"]},
                    ),
                ],
            ),
            issuer_id="primary-1",
            subject_id="sub-obao-deny-1",
            ttl_seconds=300,
        )

        denied_actions: set[str] = set()
        for d in token.spec.denied:
            denied_actions.update(d.actions)
        assert "delete" in denied_actions

        delete_cap = Capability(
            resource="secret:openbao",
            actions=["delete"],
            constraints={"openbao_paths": ["secret/data/gludd/*"]},
        )
        assert issuer.validate(token, delete_cap) is False
