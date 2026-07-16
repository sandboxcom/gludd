"""Tests for TokenQuotaEnforcer — token quotas per agent/project/scope.

Covers NF.7 STS quota enforcement (src/general_ludd/sts/quotas.py).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime

import pytest

from general_ludd.sts.quotas import (
    InMemoryQuotaBackend,
    QuotaBackend,
    QuotaConfig,
    QuotaViolation,
    StoreQuotaBackend,
    TokenQuotaEnforcer,
)


@dataclass
class _TokenRow:
    token_id: str
    agent_id: str
    project_id: str
    revoked_at: datetime | None = None
    expires_at: datetime | None = None


class _FakeStore:
    def __init__(self, rows: list[_TokenRow] | None = None) -> None:
        self._rows = rows or []

    async def list_all(self) -> list[_TokenRow]:
        return list(self._rows)


class TestQuotaConfig:
    def test_defaults_are_set(self) -> None:
        c = QuotaConfig()
        assert c.max_tokens_per_agent > 0
        assert c.max_active_tokens_per_project >= c.max_tokens_per_agent
        assert c.max_scope_width > 0

    def test_is_frozen(self) -> None:
        c = QuotaConfig()
        try:
            c.max_tokens_per_agent = 99  # type: ignore[misc]
            raised = False
        except FrozenInstanceError:
            raised = True
        assert raised, "QuotaConfig should be frozen"


class TestInMemoryQuotaBackend:
    @pytest.mark.asyncio
    async def test_counts_start_at_zero(self) -> None:
        b = InMemoryQuotaBackend()
        assert await b.active_count_for_agent("a1") == 0
        assert await b.active_count_for_project("p1") == 0

    @pytest.mark.asyncio
    async def test_record_increments_agent_and_project(self) -> None:
        b = InMemoryQuotaBackend()
        await b.record_mint("t1", "a1", "p1")
        await b.record_mint("t2", "a1", "p1")
        await b.record_mint("t3", "a2", "p1")
        assert await b.active_count_for_agent("a1") == 2
        assert await b.active_count_for_agent("a2") == 1
        assert await b.active_count_for_project("p1") == 3

    @pytest.mark.asyncio
    async def test_release_decrements(self) -> None:
        b = InMemoryQuotaBackend()
        await b.record_mint("t1", "a1", "p1")
        await b.record_mint("t2", "a1", "p1")
        await b.record_revoke("t1", "a1", "p1")
        assert await b.active_count_for_agent("a1") == 1
        assert await b.active_count_for_project("p1") == 1

    @pytest.mark.asyncio
    async def test_release_unknown_token_is_noop(self) -> None:
        b = InMemoryQuotaBackend()
        await b.record_revoke("ghost", "a1", "p1")
        assert await b.active_count_for_agent("a1") == 0


class TestTokenQuotaEnforcerScope:
    @pytest.mark.asyncio
    async def test_scope_within_limit_allowed(self) -> None:
        e = TokenQuotaEnforcer(QuotaConfig(max_scope_width=5))
        await e.check("a1", "p1", {"read", "write"})

    @pytest.mark.asyncio
    async def test_scope_too_wide_raises(self) -> None:
        e = TokenQuotaEnforcer(QuotaConfig(max_scope_width=2))
        wide = {"a", "b", "c"}
        with pytest.raises(QuotaViolation, match="scope"):
            await e.check("a1", "p1", wide)

    @pytest.mark.asyncio
    async def test_scope_accepts_list_input(self) -> None:
        e = TokenQuotaEnforcer(QuotaConfig(max_scope_width=5))
        await e.check("a1", "p1", ["read", "write"])

    @pytest.mark.asyncio
    async def test_empty_scope_allowed(self) -> None:
        e = TokenQuotaEnforcer(QuotaConfig(max_scope_width=0))
        await e.check("a1", "p1", set())


class TestTokenQuotaEnforcerAgent:
    @pytest.mark.asyncio
    async def test_first_token_allowed(self) -> None:
        e = TokenQuotaEnforcer(QuotaConfig(max_tokens_per_agent=1))
        await e.check("a1", "p1", {"read"})

    @pytest.mark.asyncio
    async def test_at_limit_raises(self) -> None:
        backend = InMemoryQuotaBackend()
        await backend.record_mint("t1", "a1", "p1")
        e = TokenQuotaEnforcer(QuotaConfig(max_tokens_per_agent=1), backend)
        with pytest.raises(QuotaViolation, match="agent"):
            await e.check("a1", "p1", {"read"})

    @pytest.mark.asyncio
    async def test_different_agents_independent(self) -> None:
        backend = InMemoryQuotaBackend()
        await backend.record_mint("t1", "a1", "p1")
        e = TokenQuotaEnforcer(QuotaConfig(max_tokens_per_agent=1), backend)
        await e.check("a2", "p1", {"read"})


class TestTokenQuotaEnforcerProject:
    @pytest.mark.asyncio
    async def test_at_project_limit_raises(self) -> None:
        backend = InMemoryQuotaBackend()
        await backend.record_mint("t1", "a1", "p1")
        e = TokenQuotaEnforcer(
            QuotaConfig(max_active_tokens_per_project=1), backend
        )
        with pytest.raises(QuotaViolation, match="project"):
            await e.check("a2", "p1", {"read"})

    @pytest.mark.asyncio
    async def test_different_projects_independent(self) -> None:
        backend = InMemoryQuotaBackend()
        await backend.record_mint("t1", "a1", "p1")
        e = TokenQuotaEnforcer(
            QuotaConfig(max_active_tokens_per_project=1), backend
        )
        await e.check("a2", "p2", {"read"})


class TestRecordMintRevoke:
    @pytest.mark.asyncio
    async def test_record_mint_checks_then_records(self) -> None:
        backend = InMemoryQuotaBackend()
        e = TokenQuotaEnforcer(QuotaConfig(max_tokens_per_agent=2), backend)
        await e.record_mint("t1", "a1", "p1", {"read"})
        assert await backend.active_count_for_agent("a1") == 1

    @pytest.mark.asyncio
    async def test_record_mint_at_limit_raises_without_recording(self) -> None:
        backend = InMemoryQuotaBackend()
        e = TokenQuotaEnforcer(QuotaConfig(max_tokens_per_agent=1), backend)
        await e.record_mint("t1", "a1", "p1", {"read"})
        with pytest.raises(QuotaViolation):
            await e.record_mint("t2", "a1", "p1", {"read"})
        assert await backend.active_count_for_agent("a1") == 1

    @pytest.mark.asyncio
    async def test_record_revoke_frees_slot(self) -> None:
        backend = InMemoryQuotaBackend()
        e = TokenQuotaEnforcer(QuotaConfig(max_tokens_per_agent=1), backend)
        await e.record_mint("t1", "a1", "p1", {"read"})
        await e.record_revoke("t1", "a1", "p1")
        await e.record_mint("t2", "a1", "p1", {"read"})
        assert await backend.active_count_for_agent("a1") == 1


class TestStoreQuotaBackend:
    @pytest.mark.asyncio
    async def test_counts_active_tokens_per_agent(self) -> None:
        rows = [
            _TokenRow("t1", "a1", "p1"),
            _TokenRow("t2", "a1", "p1"),
            _TokenRow("t3", "a1", "p1", revoked_at=datetime.now(UTC)),
        ]
        backend = StoreQuotaBackend(
            _FakeStore(rows), project_of=lambda r: r.project_id
        )
        assert await backend.active_count_for_agent("a1") == 2

    @pytest.mark.asyncio
    async def test_counts_active_tokens_per_project(self) -> None:
        rows = [
            _TokenRow("t1", "a1", "p1"),
            _TokenRow("t2", "a2", "p1"),
            _TokenRow("t3", "a3", "p2"),
            _TokenRow("t4", "a4", "p1", revoked_at=datetime.now(UTC)),
        ]
        backend = StoreQuotaBackend(
            _FakeStore(rows), project_of=lambda r: r.project_id
        )
        assert await backend.active_count_for_project("p1") == 2
        assert await backend.active_count_for_project("p2") == 1

    @pytest.mark.asyncio
    async def test_treats_expired_as_inactive(self) -> None:
        past = datetime(2000, 1, 1, tzinfo=UTC)
        rows = [
            _TokenRow("t1", "a1", "p1"),
            _TokenRow("t2", "a1", "p1", expires_at=past),
        ]
        backend = StoreQuotaBackend(
            _FakeStore(rows), project_of=lambda r: r.project_id
        )
        assert await backend.active_count_for_agent("a1") == 1

    @pytest.mark.asyncio
    async def test_record_is_noop_for_store_backend(self) -> None:
        backend = StoreQuotaBackend(_FakeStore(), project_of=lambda r: "p1")
        await backend.record_mint("t1", "a1", "p1")
        await backend.record_revoke("t1", "a1", "p1")
        assert await backend.active_count_for_agent("a1") == 0


class TestQuotaBackendProtocol:
    def test_store_backend_is_quota_backend(self) -> None:
        b: QuotaBackend = StoreQuotaBackend(_FakeStore(), project_of=lambda r: "p1")
        assert isinstance(b, QuotaBackend)

    def test_inmemory_backend_is_quota_backend(self) -> None:
        b: QuotaBackend = InMemoryQuotaBackend()
        assert isinstance(b, QuotaBackend)
