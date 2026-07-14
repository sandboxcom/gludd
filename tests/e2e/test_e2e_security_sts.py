"""E2E tests for STS token lifecycle — mint, resolve, revoke, expiry, delegation.

Exercises the real STSRegistry, StsIssuer, and StsAuditLog with
deterministic fake clocks, real PermissionSpec intersection, and real
token-lifecycle transitions. Avoids duplicating unit-level shape assertions
(tests/unit/test_sts_issuer.py covers issue/reject/ttl-cap/record-use).
"""
from __future__ import annotations

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)
from general_ludd.security.sts import (
    StsAuditLog,
    StsIssuer,
    STSRegistry,
    StsToken,
)


class FixedClock:
    """Deterministic clock for expiry tests. Advances only when told."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _issuer_spec(
    actions: list[str] | None = None,
    max_ttl: int = 3600,
    denied: list[Capability] | None = None,
) -> PermissionSpec:
    return PermissionSpec(
        agent_type="primary",
        capabilities=[
            Capability(
                resource="file:tmp",
                actions=actions or ["read", "write"],
                constraints={"path_prefix": "/tmp/gludd/"},
            ),
        ],
        denied=denied or [],
        max_sts_ttl_seconds=max_ttl,
    )


def _subject_spec(
    actions: list[str] | None = None,
    denied: list[Capability] | None = None,
) -> PermissionSpec:
    return PermissionSpec(
        agent_type="subagent",
        capabilities=[
            Capability(
                resource="file:tmp",
                actions=actions or ["read"],
                constraints={"path_prefix": "/tmp/gludd/sub/"},
            ),
        ],
        denied=denied or [],
    )


# ------------------------------------------------------------------
# STSRegistry — mint / resolve / revoke / expire / purge
# ------------------------------------------------------------------


class TestSTSRegistryLifecycle:
    def test_mint_resolve_revoke_cycle(self) -> None:
        """Full token lifecycle: mint → resolve returns claim → revoke → resolve
        returns None."""
        registry = STSRegistry()
        spec = _issuer_spec()
        token_id = registry.issue(agent_type="subagent", spec=spec, ttl_seconds=60)

        claim = registry.resolve(token_id)
        assert claim is not None
        assert claim.agent_type == "subagent"
        assert claim.spec == spec
        assert claim.issued_at < claim.expires_at

        dropped = registry.revoke(token_id)
        assert dropped is True

        assert registry.resolve(token_id) is None

    def test_revoke_unknown_token_returns_false(self) -> None:
        registry = STSRegistry()
        assert registry.revoke("nonexistent-token") is False

    def test_expired_token_returns_none_and_is_evicted(self) -> None:
        clock = FixedClock(1000.0)
        registry = STSRegistry(clock=clock)
        spec = _issuer_spec()
        token_id = registry.issue(
            agent_type="subagent", spec=spec, ttl_seconds=10
        )

        claim = registry.resolve(token_id)
        assert claim is not None

        clock.advance(11)
        assert registry.resolve(token_id) is None

        assert registry.resolve(token_id) is None

    def test_unknown_token_returns_none_fail_closed(self) -> None:
        registry = STSRegistry()
        assert registry.resolve("bogus-token-that-was-never-minted") is None

    def test_purge_expired_drops_all_expired(self) -> None:
        clock = FixedClock(1000.0)
        registry = STSRegistry(clock=clock)
        spec = _issuer_spec()

        t1 = registry.issue("subagent", spec, ttl_seconds=5)
        t2 = registry.issue("subagent", spec, ttl_seconds=5)
        t3 = registry.issue("subagent", spec, ttl_seconds=30)

        clock.advance(10)
        purged = registry.purge_expired()
        assert purged == 2

        assert registry.resolve(t1) is None
        assert registry.resolve(t2) is None
        assert registry.resolve(t3) is not None


# ------------------------------------------------------------------
# StsIssuer — delegation, denials, permission narrowing, revoke
# ------------------------------------------------------------------


class TestStsIssuerDelegation:
    def test_issue_then_get_token_returns_live_token(self) -> None:
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=_issuer_spec(max_ttl=600),
            subject_spec_request=_subject_spec(),
            issuer_id="primary-1",
            subject_id="sub-1",
            ttl_seconds=300,
        )
        assert isinstance(token, StsToken)
        assert token.issuer_agent_id == "primary-1"
        assert token.subject_agent_id == "sub-1"

        retrieved = issuer.get_token(token.token_id)
        assert retrieved is not None
        assert retrieved.token_id == token.token_id

    def test_delegated_token_inherits_issuer_denials(self) -> None:
        """C-SEC-1: A denial on the issuer propagates into the child token
        via union of denied lists, so a carve-out can never be dropped."""
        issuer_denied = [
            Capability(resource="file:tmp", actions=["delete"], constraints={})
        ]
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=_issuer_spec(denied=issuer_denied),
            subject_spec_request=_subject_spec(),
            issuer_id="primary-1",
            subject_id="sub-1",
            ttl_seconds=300,
        )
        assert len(token.spec.denied) >= 1
        denied_resources = {d.resource for d in token.spec.denied}
        assert "file:tmp" in denied_resources

    def test_delegated_token_merges_subject_and_issuer_denials(self) -> None:
        """Both issuer and subject spec carry denials — child token carries
        the union, never drops either."""
        issuer_denied = [
            Capability(resource="file:tmp", actions=["delete"], constraints={})
        ]
        subject_denied = [
            Capability(resource="file:tmp", actions=["write"], constraints={})
        ]
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=_issuer_spec(denied=issuer_denied),
            subject_spec_request=_subject_spec(denied=subject_denied),
            issuer_id="primary-1",
            subject_id="sub-1",
            ttl_seconds=300,
        )
        denied_actions = set()
        for d in token.spec.denied:
            denied_actions.update(d.actions)
        assert "delete" in denied_actions
        assert "write" in denied_actions

    def test_permission_narrowing_via_intersection(self) -> None:
        """An STS token spec must be a narrowing of the original spec.
        Using PermissionSpecParser.intersection for the human+agent case."""
        agent_spec = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="file:tmp",
                    actions=["read", "write"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                ),
            ],
        )
        narrower_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="file:tmp",
                    actions=["read"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                ),
            ],
        )
        assert PermissionSpecParser.is_subset(narrower_spec, agent_spec) is True

        assert PermissionSpecParser.is_subset(agent_spec, narrower_spec) is False

        inter = PermissionSpecParser.intersection(agent_spec, narrower_spec)
        assert inter.subject == PermissionSubject.STS_TOKEN
        file_cap = next(
            (c for c in inter.capabilities if c.resource == "file:tmp"), None
        )
        assert file_cap is not None
        assert set(file_cap.actions) == {"read"}

    def test_validate_rejects_denied_action_even_when_broadly_granted(self) -> None:
        """C-SEC-1: A denial beats any positive grant — even when the subject
        only requests ``read``, the issuer's ``write`` denial propagates into
        the child token, so ``validate(token, write)`` must fail."""
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=_issuer_spec(
                denied=[
                    Capability(
                        resource="file:tmp", actions=["write"], constraints={}
                    )
                ]
            ),
            subject_spec_request=_subject_spec(actions=["read"]),
            issuer_id="primary-1",
            subject_id="sub-1",
            ttl_seconds=300,
        )
        read_cap = Capability(
            resource="file:tmp",
            actions=["read"],
            constraints={"path_prefix": "/tmp/gludd/sub/"},
        )
        assert issuer.validate(token, read_cap) is True

        write_cap = Capability(
            resource="file:tmp",
            actions=["write"],
            constraints={"path_prefix": "/tmp/gludd/sub/"},
        )
        assert issuer.validate(token, write_cap) is False

    def test_revoke_makes_get_token_return_none(self) -> None:
        issuer = StsIssuer()
        token = issuer.issue(
            issuer_spec=_issuer_spec(),
            subject_spec_request=_subject_spec(),
            issuer_id="primary-1",
            subject_id="sub-1",
            ttl_seconds=300,
        )
        assert issuer.get_token(token.token_id) is not None

        revoked = issuer.revoke(token.token_id)
        assert revoked is True
        assert issuer.get_token(token.token_id) is None

    def test_revoke_unknown_token_returns_false(self) -> None:
        issuer = StsIssuer()
        assert issuer.revoke("no-such-token") is False

    def test_expired_token_is_not_in_list_active(self) -> None:
        clock = FixedClock(1000.0)
        issuer = StsIssuer(clock=clock)
        t1 = issuer.issue(
            _issuer_spec(max_ttl=600),
            _subject_spec(),
            "primary-1", "sub-a",
            ttl_seconds=5,
        )
        t2 = issuer.issue(
            _issuer_spec(max_ttl=600),
            _subject_spec(),
            "primary-1", "sub-b",
            ttl_seconds=30,
        )
        clock.advance(10)
        active = issuer.list_active()
        active_ids = {t.token_id for t in active}
        assert t1.token_id not in active_ids
        assert t2.token_id in active_ids

    def test_validate_expired_token_fails(self) -> None:
        clock = FixedClock(1000.0)
        issuer = StsIssuer(clock=clock)
        token = issuer.issue(
            _issuer_spec(),
            _subject_spec(),
            "primary-1", "sub-1",
            ttl_seconds=10,
        )
        read_cap = Capability(
            resource="file:tmp",
            actions=["read"],
            constraints={"path_prefix": "/tmp/gludd/sub/"},
        )
        assert issuer.validate(token, read_cap) is True

        clock.advance(11)
        assert issuer.validate(token, read_cap) is False


# ------------------------------------------------------------------
# StsAuditLog — record, query, filtering
# ------------------------------------------------------------------


class TestStsAuditLog:
    def test_record_issue_then_query_by_agent_id(self) -> None:
        log = StsAuditLog()
        issuer = StsIssuer()
        token = issuer.issue(
            _issuer_spec(),
            _subject_spec(),
            "primary-1",
            "sub-1",
            ttl_seconds=300,
        )
        log.record_issue(token)

        events = log.query(agent_id="sub-1")
        assert len(events) == 1
        assert events[0]["event"] == "issued"
        assert events[0]["subject_agent_id"] == "sub-1"

    def test_record_use_then_query_by_capability(self) -> None:
        log = StsAuditLog()
        cap = Capability(
            resource="file:tmp",
            actions=["read"],
            constraints={"path_prefix": "/tmp/gludd/"},
        )
        log.record_use("tok-1", cap, "/tmp/gludd/config.yml")
        events = log.query(capability="file:tmp")
        assert len(events) == 1
        assert events[0]["event"] == "used"

    def test_record_expiry_event(self) -> None:
        log = StsAuditLog()
        log.record_expiry("tok-xyz")
        events = log.query()
        assert len(events) == 1
        assert events[0]["event"] == "expired"
        assert events[0]["token_id"] == "tok-xyz"

    def test_query_by_since_filters_old_events(self) -> None:
        log = StsAuditLog()
        log.record_expiry("tok-old")
        log.record_expiry("tok-new")
        events = log.query(since=9999999999.0)
        assert len(events) == 0

    def test_full_audit_cycle_issue_use_expire(self) -> None:
        log = StsAuditLog()
        issuer = StsIssuer()
        token = issuer.issue(
            _issuer_spec(),
            _subject_spec(),
            "primary-1",
            "sub-1",
            ttl_seconds=300,
        )
        log.record_issue(token)

        cap = Capability(
            resource="file:tmp",
            actions=["read"],
            constraints={"path_prefix": "/tmp/gludd/sub/"},
        )
        log.record_use(token.token_id, cap, "/tmp/gludd/sub/output.txt")
        log.record_expiry(token.token_id)

        all_events = log.query()
        assert len(all_events) == 3
        event_types = {e["event"] for e in all_events}
        assert event_types == {"issued", "used", "expired"}
