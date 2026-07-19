"""Unit tests for TokenReaper (Phase P5 — expired-token reaper + revocation cascade).

Covers the ``expire(TTL)`` branch of the token lifecycle diagram (spec §4),
which is the only lifecycle edge not handled by P1-P4. The reaper also
implements the parent→child revocation cascade required by the
"capability non-escalation" rule (spec §2): when a parent's token dies, every
child token minted under it MUST be revoked too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.reaper import TokenReaper


def _make_record(
    token_id: str,
    agent_id: str,
    parent_agent_id: str = "root",
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> MagicMock:
    rec = MagicMock()
    rec.token_id = token_id
    rec.agent_id = agent_id
    rec.parent_agent_id = parent_agent_id
    rec.role_name = f"agent-{agent_id}"
    rec.role_id = f"role-{agent_id}"
    rec.revoked_at = revoked_at
    rec.expires_at = expires_at
    return rec


class TestReapExpired:
    @pytest.mark.asyncio
    async def test_reap_revives_only_expired_live_tokens(self):
        """reap_expired() revokes tokens whose expires_at < now AND revoked_at IS NULL."""
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        expired_live = _make_record(
            "tok-1", "agent-1", expires_at=now - timedelta(minutes=5),
        )

        store = AsyncMock()
        store.list_expired.return_value = [expired_live]

        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.reap_expired(now=now)

        store.list_expired.assert_awaited_once_with(now)
        revoker.revoke.assert_awaited_once_with("agent-1")
        assert count == 1

    @pytest.mark.asyncio
    async def test_reap_returns_zero_when_nothing_expired(self):
        """reap_expired() returns 0 and does not call revoker when store is empty."""
        store = AsyncMock()
        store.list_expired.return_value = []
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        count = await reaper.reap_expired(now=now)

        revoker.revoke.assert_not_called()
        assert count == 0

    @pytest.mark.asyncio
    async def test_reap_uses_utc_now_when_now_omitted(self):
        """reap_expired() defaults now to datetime.now(UTC)."""
        store = AsyncMock()
        store.list_expired.return_value = []
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        await reaper.reap_expired()

        args = store.list_expired.call_args
        passed_dt = args.args[0]
        assert passed_dt.tzinfo == UTC

    @pytest.mark.asyncio
    async def test_reap_continues_past_individual_revoke_failures(self):
        """A failure revoking one token does not abort the sweep — others still get reaped."""
        expired_a = _make_record("tok-a", "agent-a", expires_at=datetime(2020, 1, 1, tzinfo=UTC))
        expired_b = _make_record("tok-b", "agent-b", expires_at=datetime(2020, 1, 1, tzinfo=UTC))

        store = AsyncMock()
        store.list_expired.return_value = [expired_a, expired_b]

        revoker = AsyncMock()
        revoker.revoke.side_effect = [RuntimeError("boom"), None]

        reaper = TokenReaper(store=store, revoker=revoker)
        now = datetime(2026, 7, 15, tzinfo=UTC)
        count = await reaper.reap_expired(now=now)

        assert revoker.revoke.await_count == 2
        assert count == 1

    @pytest.mark.asyncio
    async def test_reap_skips_revoked_records_returned_by_store(self):
        """Even if the store returns an already-revoked row, the reaper skips it."""
        past = datetime(2020, 1, 1, tzinfo=UTC)
        already = _make_record(
            "tok-z", "agent-z",
            expires_at=past,
            revoked_at=datetime(2021, 1, 1, tzinfo=UTC),
        )
        store = AsyncMock()
        store.list_expired.return_value = [already]
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.reap_expired(now=datetime(2026, 7, 15, tzinfo=UTC))

        revoker.revoke.assert_not_called()
        assert count == 0


class TestCascadeRevoke:
    @pytest.mark.asyncio
    async def test_cascade_revokes_all_live_children(self):
        """cascade_revoke(parent) revokes every non-revoked child of parent."""
        child_a = _make_record("tok-a", "agent-a", parent_agent_id="parent-1")
        child_b = _make_record("tok-b", "agent-b", parent_agent_id="parent-1")
        child_c_revoked = _make_record(
            "tok-c", "agent-c", parent_agent_id="parent-1",
            revoked_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

        store = AsyncMock()
        # First call explores parent-1's direct children; subsequent calls
        # are the recursion into each revoked live child (both return empty,
        # meaning agent-a/agent-b have no descendants of their own).
        store.list_children.side_effect = [
            [child_a, child_b, child_c_revoked],
            [],
            [],
        ]
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.cascade_revoke("parent-1")

        first_call = store.list_children.await_args_list[0]
        assert first_call.args == ("parent-1",)
        revoked_ids = {c.args[0] for c in revoker.revoke.await_args_list}
        assert revoked_ids == {"agent-a", "agent-b"}
        assert count == 2

    @pytest.mark.asyncio
    async def test_cascade_returns_zero_when_no_children(self):
        """cascade_revoke(parent) with no child tokens is a no-op."""
        store = AsyncMock()
        store.list_children.return_value = []
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.cascade_revoke("parent-orphan")

        revoker.revoke.assert_not_called()
        assert count == 0

    @pytest.mark.asyncio
    async def test_cascade_recurses_into_grandchildren(self):
        """cascade_revoke follows the parent chain transitively.

        When child-a has its own children, those grandchildren are revoked
        as well. This enforces capability non-escalation across the full
        delegation subtree, not just one level.
        """
        child = _make_record("tok-child", "agent-child", parent_agent_id="parent-1")
        grandchild_1 = _make_record("tok-g1", "agent-g1", parent_agent_id="agent-child")
        grandchild_2 = _make_record("tok-g2", "agent-g2", parent_agent_id="agent-child")

        store = AsyncMock()
        store.list_children.side_effect = [
            [child],
            [grandchild_1, grandchild_2],
            [],
            [],
        ]
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.cascade_revoke("parent-1")

        revoked_ids = {c.args[0] for c in revoker.revoke.await_args_list}
        assert revoked_ids == {"agent-child", "agent-g1", "agent-g2"}
        assert count == 3

    @pytest.mark.asyncio
    async def test_cascade_terminates_on_already_revoked_descendant(self):
        """If a child is already revoked, its subtree is not descended into."""
        child_revoked = _make_record(
            "tok-cr", "agent-cr", parent_agent_id="parent-1",
            revoked_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

        store = AsyncMock()
        store.list_children.return_value = [child_revoked]
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.cascade_revoke("parent-1")

        # Already-revoked child should not be re-revoked, and its subtree
        # must not be explored (would otherwise re-revoke dead tokens).
        revoker.revoke.assert_not_called()
        assert count == 0
        assert store.list_children.await_count == 1

    @pytest.mark.asyncio
    async def test_cascade_guard_against_cycles(self):
        """A malformed cycle in parent_agent_id pointers does not loop forever.

        Tokens are normally parent→child (acyclic), but a defensive
        visited-set guarantees termination even if the data is corrupt.
        """
        a = _make_record("tok-a", "agent-a", parent_agent_id="parent-1")
        b = _make_record("tok-b", "agent-b", parent_agent_id="agent-a")
        a_again = _make_record("tok-a", "agent-a", parent_agent_id="agent-b")

        store = AsyncMock()
        store.list_children.side_effect = [
            [a],
            [b],
            [a_again],
        ]
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.cascade_revoke("parent-1")

        assert count == 2
        assert store.list_children.await_count <= 3


class TestReaperAudit:
    @pytest.mark.asyncio
    async def test_reap_records_expire_audit_event(self):
        """reap_expired() emits an 'expire' audit event for each reaped token."""
        expired = _make_record(
            "tok-e", "agent-e", parent_agent_id="parent-1",
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        store = AsyncMock()
        store.list_expired.return_value = [expired]
        revoker = AsyncMock()
        audit = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker, audit_pipeline=audit)
        await reaper.reap_expired(now=datetime(2026, 7, 15, tzinfo=UTC))

        audit.record_expire.assert_awaited_once_with(
            token_id="tok-e",
            agent_id="agent-e",
            parent_agent_id="parent-1",
        )

    @pytest.mark.asyncio
    async def test_reap_runs_without_audit_pipeline(self):
        """When no audit_pipeline is wired, reaping still completes."""
        expired = _make_record(
            "tok-x", "agent-x", expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        store = AsyncMock()
        store.list_expired.return_value = [expired]
        revoker = AsyncMock()

        reaper = TokenReaper(store=store, revoker=revoker)
        count = await reaper.reap_expired(now=datetime(2026, 7, 15, tzinfo=UTC))

        assert count == 1
