"""Tests for TokenReaper — TTL sweep + cascade revocation (sts/reaper.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from general_ludd.sts.reaper import TokenReaper


@dataclass
class _Record:
    agent_id: str
    token_id: str
    parent_agent_id: str | None = None
    revoked_at: datetime | None = None


class _FakeStore:
    def __init__(
        self,
        expired: list[_Record] | None = None,
        children: dict[str, list[_Record]] | None = None,
    ) -> None:
        self._expired = expired or []
        self._children = children or {}

    async def list_expired(self, now: datetime) -> list[_Record]:
        return self._expired

    async def list_children(self, parent_agent_id: str) -> list[_Record]:
        return self._children.get(parent_agent_id, [])


class _FakeRevoker:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.revoked: list[str] = []
        self._fail_for = fail_for or set()

    async def revoke(self, agent_id: str) -> None:
        if agent_id in self._fail_for:
            raise RuntimeError(f"revoke failed for {agent_id}")
        self.revoked.append(agent_id)


class _FakeAudit:
    def __init__(self) -> None:
        self.expire_events: list[dict[str, str | None]] = []

    async def record_expire(
        self,
        token_id: str,
        agent_id: str,
        parent_agent_id: str | None,
    ) -> None:
        self.expire_events.append(
            {
                "token_id": token_id,
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
            }
        )


class TestReapExpired:
    @pytest.mark.asyncio
    async def test_reaps_all_expired_live_tokens(self) -> None:
        store = _FakeStore(
            expired=[_Record("a1", "t1"), _Record("a2", "t2")]
        )
        revoker = _FakeRevoker()
        reaper = TokenReaper(store, revoker)

        count = await reaper.reap_expired(now=datetime.now(UTC))

        assert count == 2
        assert revoker.revoked == ["a1", "a2"]

    @pytest.mark.asyncio
    async def test_skips_already_revoked_tokens(self) -> None:
        store = _FakeStore(
            expired=[
                _Record("a1", "t1", revoked_at=datetime.now(UTC)),
                _Record("a2", "t2"),
            ]
        )
        revoker = _FakeRevoker()
        reaper = TokenReaper(store, revoker)

        count = await reaper.reap_expired()

        assert count == 1
        assert revoker.revoked == ["a2"]

    @pytest.mark.asyncio
    async def test_revoke_failure_does_not_abort_sweep(self) -> None:
        store = _FakeStore(
            expired=[_Record("bad", "t1"), _Record("good", "t2")]
        )
        revoker = _FakeRevoker(fail_for={"bad"})
        reaper = TokenReaper(store, revoker)

        count = await reaper.reap_expired()

        assert count == 1
        assert revoker.revoked == ["good"]

    @pytest.mark.asyncio
    async def test_emits_expire_audit_event_per_reaped_token(self) -> None:
        store = _FakeStore(expired=[_Record("a1", "t1", parent_agent_id="root")])
        revoker = _FakeRevoker()
        audit = _FakeAudit()
        reaper = TokenReaper(store, revoker, audit_pipeline=audit)

        count = await reaper.reap_expired()

        assert count == 1
        assert audit.expire_events == [
            {"token_id": "t1", "agent_id": "a1", "parent_agent_id": "root"}
        ]

    @pytest.mark.asyncio
    async def test_no_expired_tokens_returns_zero(self) -> None:
        reaper = TokenReaper(_FakeStore(), _FakeRevoker())

        assert await reaper.reap_expired() == 0


class TestCascadeRevoke:
    @pytest.mark.asyncio
    async def test_revokes_direct_children(self) -> None:
        store = _FakeStore(
            children={"root": [_Record("c1", "t1"), _Record("c2", "t2")]}
        )
        revoker = _FakeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")

        assert total == 2
        assert set(revoker.revoked) == {"c1", "c2"}

    @pytest.mark.asyncio
    async def test_revokes_transitively_through_grandchildren(self) -> None:
        store = _FakeStore(
            children={
                "root": [_Record("c1", "t1")],
                "c1": [_Record("g1", "t2")],
            }
        )
        revoker = _FakeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")

        assert total == 2
        assert set(revoker.revoked) == {"c1", "g1"}

    @pytest.mark.asyncio
    async def test_prunes_already_revoked_subtrees(self) -> None:
        dead = _Record("c1", "t1", revoked_at=datetime.now(UTC))
        store = _FakeStore(
            children={
                "root": [dead],
                "c1": [_Record("g1", "t2")],
            }
        )
        revoker = _FakeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")

        assert total == 0
        assert revoker.revoked == []

    @pytest.mark.asyncio
    async def test_terminates_on_parent_pointer_cycle(self) -> None:
        store = _FakeStore(
            children={
                "root": [_Record("a", "t1")],
                "a": [_Record("root", "t0"), _Record("a", "t1")],
            }
        )
        revoker = _FakeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")

        assert total >= 1
        assert "a" in revoker.revoked
