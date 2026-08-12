"""Deep tests for sts — STSRegistry, StsIssuer, StsAuditLog token lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.security.permissions import (
    Capability,
    PermissionDeniedError,
    PermissionSpec,
)
from general_ludd.security.sts import (
    DEFAULT_TTL_SECONDS,
    StsAuditLog,
    StsIssuer,
    STSRegistry,
    StsToken,
)


def make_spec(
    capabilities: list[Capability] | None = None,
    denied: list[Capability] | None = None,
    max_sts_ttl_seconds: int = 3600,
) -> PermissionSpec:
    return PermissionSpec(
        agent_type="test",
        capabilities=capabilities or [],
        denied=denied or [],
        max_sts_ttl_seconds=max_sts_ttl_seconds,
    )


def file_cap(actions: list[str], path_prefix: str = "/repo/") -> Capability:
    """Return a structurally valid file capability for delegation tests."""
    return Capability(
        resource="file:repo",
        actions=actions,
        constraints={"path_prefix": path_prefix},
    )


class TestSTSRegistry:
    def test_issue_and_resolve(self) -> None:
        registry = STSRegistry()
        spec = make_spec(capabilities=[Capability(resource="*", actions=["read"])])
        token_id = registry.issue("build", spec, ttl_seconds=3600)
        claim = registry.resolve(token_id)
        assert claim is not None
        assert claim.agent_type == "build"
        assert claim.spec == spec
        assert claim.expires_at > claim.issued_at
        assert claim.spec.capability_for("*") is not None

    def test_resolve_unknown(self) -> None:
        registry = STSRegistry()
        assert registry.resolve("nonexistent") is None

    def test_resolve_expired(self) -> None:
        clock = MagicMock()
        clock.return_value = 100.0
        registry = STSRegistry(clock=clock)
        spec = make_spec()
        token_id = registry.issue("build", spec, ttl_seconds=60)
        assert registry.resolve(token_id) is not None
        clock.return_value = 200.0
        assert registry.resolve(token_id) is None
        assert registry.resolve(token_id) is None

    def test_revoke(self) -> None:
        registry = STSRegistry()
        spec = make_spec()
        token_id = registry.issue("build", spec)
        assert registry.revoke(token_id)
        assert registry.resolve(token_id) is None

    def test_revoke_unknown(self) -> None:
        registry = STSRegistry()
        assert not registry.revoke("nonexistent")

    def test_purge_expired(self) -> None:
        clock = MagicMock()
        clock.return_value = 100.0
        registry = STSRegistry(clock=clock)
        spec = make_spec()
        registry.issue("a", spec, ttl_seconds=60)
        registry.issue("b", spec, ttl_seconds=60)
        clock.return_value = 200.0
        purged = registry.purge_expired()
        assert purged == 2

    def test_purge_only_expired(self) -> None:
        clock = MagicMock()
        clock.return_value = 100.0
        registry = STSRegistry(clock=clock)
        spec = make_spec()
        registry.issue("a", spec, ttl_seconds=100)
        registry.issue("b", spec, ttl_seconds=30)
        clock.return_value = 150.0
        purged = registry.purge_expired()
        assert purged == 1

    def test_lazy_eviction_on_resolve(self) -> None:
        clock = MagicMock()
        clock.return_value = 100.0
        registry = STSRegistry(clock=clock)
        spec = make_spec()
        token_id = registry.issue("build", spec, ttl_seconds=10)
        clock.return_value = 200.0
        assert registry.resolve(token_id) is None
        assert token_id not in registry._claims

    def test_default_ttl(self) -> None:
        registry = STSRegistry()
        token_id = registry.issue("build", make_spec())
        claim = registry.resolve(token_id)
        assert claim is not None
        assert claim.expires_at - claim.issued_at == DEFAULT_TTL_SECONDS

    def test_claim_frozen(self) -> None:
        registry = STSRegistry()
        token_id = registry.issue("build", make_spec())
        claim = registry.resolve(token_id)
        with pytest.raises(AttributeError):
            claim.agent_type = "other"  # type: ignore[misc]


class TestStsIssuer:
    @pytest.fixture
    def issuer_spec(self) -> PermissionSpec:
        return make_spec(
            capabilities=[
                file_cap(["read"]),
                file_cap(["write"]),
            ],
            max_sts_ttl_seconds=3600,
        )

    @pytest.fixture
    def issuer(self) -> StsIssuer:
        return StsIssuer()

    def test_issue_token(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        subject_spec = make_spec(
            capabilities=[file_cap(["read"], "/repo/work/")],
            max_sts_ttl_seconds=3600,
        )
        token = issuer.issue(
            issuer_spec=issuer_spec,
            subject_spec_request=subject_spec,
            issuer_id="issuer-1",
            subject_id="subject-1",
            ttl_seconds=600,
        )
        assert token.issuer_agent_id == "issuer-1"
        assert token.subject_agent_id == "subject-1"
        assert token.expires_at > token.issued_at
        assert len(token.token_id) == 32

    def test_issue_superset_denied(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        subject_spec = make_spec(
            capabilities=[Capability(resource="*", actions=["delete"])],
            max_sts_ttl_seconds=3600,
        )
        with pytest.raises(PermissionDeniedError):
            issuer.issue(
                issuer_spec=issuer_spec,
                subject_spec_request=subject_spec,
                issuer_id="i",
                subject_id="s",
                ttl_seconds=600,
            )

    def test_issue_uses_matching_grant_when_resource_is_repeated(
        self, issuer: StsIssuer
    ) -> None:
        """A later, narrower matching grant must not be hidden by the first one."""
        issuer_spec = make_spec(
            capabilities=[
                Capability(
                    resource="file:repo",
                    actions=["read"],
                    constraints={"path_prefix": "/repo/"},
                ),
                Capability(
                    resource="file:repo",
                    actions=["write"],
                    constraints={"path_prefix": "/repo/"},
                ),
            ]
        )
        subject_spec = make_spec(
            capabilities=[
                Capability(
                    resource="file:repo",
                    actions=["write"],
                    constraints={"path_prefix": "/repo/work/"},
                )
            ]
        )

        token = issuer.issue(issuer_spec, subject_spec, "issuer", "subject", 60)

        assert token.spec == subject_spec

    def test_ttl_clamped(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        subject_spec = make_spec(
            capabilities=[file_cap(["read"], "/repo/work/")],
            max_sts_ttl_seconds=3600,
        )
        token = issuer.issue(
            issuer_spec=issuer_spec,
            subject_spec_request=subject_spec,
            issuer_id="i",
            subject_id="s",
            ttl_seconds=9999,
        )
        assert token.expires_at - token.issued_at <= 3600

    def test_validate_valid_token(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        subject_spec = make_spec(
            capabilities=[file_cap(["read"], "/repo/work/")],
            max_sts_ttl_seconds=3600,
        )
        token = issuer.issue(issuer_spec, subject_spec, "i", "s", 600)
        assert issuer.validate(token, Capability(resource="file:repo", actions=["read"]))

    def test_validate_missing_capability(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        subject_spec = make_spec(
            capabilities=[file_cap(["read"], "/repo/work/")],
            max_sts_ttl_seconds=3600,
        )
        token = issuer.issue(issuer_spec, subject_spec, "i", "s", 600)
        assert not issuer.validate(token, Capability(resource="file:repo", actions=["write"]))

    def test_validate_expired_token(self, issuer_spec: PermissionSpec) -> None:
        clock = MagicMock(return_value=100.0)
        issuer = StsIssuer(clock=clock)
        subject_spec = make_spec(
            capabilities=[file_cap(["read"], "/repo/work/")],
            max_sts_ttl_seconds=3600,
        )
        token = issuer.issue(issuer_spec, subject_spec, "i", "s", 60)
        assert issuer.validate(token, Capability(resource="file:repo", actions=["read"]))
        clock.return_value = 200.0
        assert not issuer.validate(token, Capability(resource="file:repo", actions=["read"]))

    def test_validate_denied_action(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        issuer_spec = make_spec(
            capabilities=[file_cap(["read", "write", "delete"])],
            max_sts_ttl_seconds=3600,
        )
        subject_spec = make_spec(
            capabilities=[file_cap(["read", "delete"], "/repo/work/")],
            denied=[file_cap(["delete"], "/repo/work/")],
            max_sts_ttl_seconds=3600,
        )
        token = issuer.issue(issuer_spec, subject_spec, "i", "s", 600)
        assert not issuer.validate(token, Capability(resource="file:repo", actions=["delete"]))

    def test_record_use(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        token = issuer.issue(
            issuer_spec,
            make_spec(capabilities=[file_cap(["read"], "/repo/work/")]),
            "i",
            "s",
            600,
        )
        issuer.record_use(token.token_id)
        updated = issuer.get_token(token.token_id)
        assert updated is not None
        assert updated.use_count == 1
        assert updated.last_used_at is not None

    def test_record_use_unknown(self) -> None:
        issuer = StsIssuer()
        issuer.record_use("nonexistent")

    def test_get_token(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        token = issuer.issue(
            issuer_spec,
            make_spec(capabilities=[file_cap(["read"], "/repo/work/")]),
            "i",
            "s",
            600,
        )
        assert issuer.get_token(token.token_id) is not None

    def test_get_token_expired(self, issuer_spec: PermissionSpec) -> None:
        clock = MagicMock(return_value=100.0)
        issuer = StsIssuer(clock=clock)
        token = issuer.issue(
            issuer_spec,
            make_spec(capabilities=[file_cap(["read"], "/repo/work/")]),
            "i",
            "s",
            60,
        )
        clock.return_value = 200.0
        assert issuer.get_token(token.token_id) is None

    def test_list_active(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        subject_spec = make_spec(capabilities=[file_cap(["read"], "/repo/work/")])
        issuer.issue(issuer_spec, subject_spec, "i", "s1", 600)
        issuer.issue(issuer_spec, subject_spec, "i", "s2", 600)
        active = issuer.list_active()
        assert len(active) == 2

    def test_list_active_evicts_expired(self) -> None:
        clock = MagicMock(return_value=100.0)
        issuer = StsIssuer(clock=clock)
        spec = make_spec(capabilities=[file_cap(["read"])])
        issuer.issue(spec, spec, "i", "s1", 30)
        issuer.issue(spec, spec, "i", "s2", 100)
        clock.return_value = 150.0
        active = issuer.list_active()
        assert len(active) == 1

    def test_revoke(self, issuer: StsIssuer, issuer_spec: PermissionSpec) -> None:
        token = issuer.issue(
            issuer_spec,
            make_spec(capabilities=[file_cap(["read"], "/repo/work/")]),
            "i",
            "s",
            600,
        )
        assert issuer.revoke(token.token_id)
        assert issuer.get_token(token.token_id) is None

    def test_revoke_unknown(self) -> None:
        issuer = StsIssuer()
        assert not issuer.revoke("nonexistent")


class TestStsAuditLog:
    @pytest.fixture
    def log(self) -> StsAuditLog:
        return StsAuditLog()

    def _make_token(self) -> StsToken:
        return StsToken(
            token_id="tok-1",
            issuer_agent_id="i-1",
            subject_agent_id="s-1",
            spec=make_spec(),
            issued_at=100.0,
            expires_at=200.0,
        )

    def test_record_issue(self, log: StsAuditLog) -> None:
        token = self._make_token()
        log.record_issue(token)
        assert len(log._events) == 1
        assert log._events[0]["event"] == "issued"

    def test_record_use(self, log: StsAuditLog) -> None:
        token = self._make_token()
        log.record_issue(token)
        log.record_use("tok-1", Capability(resource="file.txt", actions=["read"]), "/tmp/file.txt")
        assert len(log._events) == 2
        assert log._events[1]["event"] == "used"
        assert log._events[1]["capability"] == "file.txt"

    def test_record_expiry(self, log: StsAuditLog) -> None:
        token = self._make_token()
        log.record_issue(token)
        log.record_expiry("tok-1")
        assert log._events[1]["event"] == "expired"

    def test_query_by_agent(self, log: StsAuditLog) -> None:
        token = self._make_token()
        log.record_issue(token)
        results = log.query(agent_id="s-1")
        assert len(results) >= 1

    def test_query_by_capability(self, log: StsAuditLog) -> None:
        token = self._make_token()
        log.record_issue(token)
        log.record_use("tok-1", Capability(resource="files/secret", actions=["read"]), "/tmp/x")
        results = log.query(capability="files/secret")
        assert len(results) == 1

    def test_query_by_since(self, log: StsAuditLog) -> None:
        token = self._make_token()
        log.record_issue(token)
        results = log.query(since=50.0)
        assert len(results) >= 1
        results = log.query(since=999.0)
        assert len(results) == 0

    def test_query_no_matches(self, log: StsAuditLog) -> None:
        assert log.query(agent_id="nonexistent") == []


class TestDefaultTtl:
    def test_default_value(self) -> None:
        assert DEFAULT_TTL_SECONDS == 3600
