"""TDD tests for wiring TokenReaper into the daemon's periodic tick.

Covers three integration surfaces:
  1. ``EventLoop._phase_reap_expired_sts_tokens`` — the periodic tick phase
     that calls ``TokenReaper.reap_expired()`` every ``sts_reap_interval_ticks``.
  2. ``TokenRevoker.revoke()`` post-revoke cascade hook — when a parent token
     is revoked, its delegation subtree is cascade-revoked.
  3. ``daemon._build_sts_reaper`` — the factory that composes TokenStore +
     TokenRevoker + TokenReaper + StsAuditPipeline and wires the cascade hook.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop, PHASE_ORDER
from general_ludd.sts.reaper import TokenReaper
from general_ludd.sts.revoker import TokenRevoker


class TestReapExpiredPhase:
    """EventLoop._phase_reap_expired_sts_tokens behaviour."""

    @pytest.mark.asyncio
    async def test_phase_calls_reap_expired_when_wired(self) -> None:
        reaper = AsyncMock(spec=TokenReaper)
        reaper.reap_expired.return_value = 3
        loop = EventLoop(
            config={"sts_reap_interval_ticks": 1},
            daemon_state={"_sts_reaper": reaper},
        )
        loop._total_ticks = 1
        await loop._phase_reap_expired_sts_tokens()
        reaper.reap_expired.assert_awaited_once()
        assert loop._tick_metrics.get("sts_tokens_reaped") == 3

    @pytest.mark.asyncio
    async def test_phase_noop_when_reaper_absent(self) -> None:
        loop = EventLoop(
            config={"sts_reap_interval_ticks": 1},
            daemon_state={},
        )
        loop._total_ticks = 1
        await loop._phase_reap_expired_sts_tokens()
        assert "sts_tokens_reaped" not in loop._tick_metrics

    @pytest.mark.asyncio
    async def test_phase_respects_interval(self) -> None:
        reaper = AsyncMock(spec=TokenReaper)
        reaper.reap_expired.return_value = 0
        loop = EventLoop(
            config={"sts_reap_interval_ticks": 60},
            daemon_state={"_sts_reaper": reaper},
        )
        loop._total_ticks = 30
        await loop._phase_reap_expired_sts_tokens()
        reaper.reap_expired.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase_disabled_when_interval_zero(self) -> None:
        reaper = AsyncMock(spec=TokenReaper)
        loop = EventLoop(
            config={"sts_reap_interval_ticks": 0},
            daemon_state={"_sts_reaper": reaper},
        )
        loop._total_ticks = 60
        await loop._phase_reap_expired_sts_tokens()
        reaper.reap_expired.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase_survives_reaper_exception(self) -> None:
        reaper = AsyncMock(spec=TokenReaper)
        reaper.reap_expired.side_effect = RuntimeError("db down")
        loop = EventLoop(
            config={"sts_reap_interval_ticks": 1},
            daemon_state={"_sts_reaper": reaper},
        )
        loop._total_ticks = 1
        await loop._phase_reap_expired_sts_tokens()
        reaper.reap_expired.assert_awaited_once()

    def test_phase_registered_in_phase_order(self) -> None:
        assert "reap_expired_sts_tokens" in PHASE_ORDER


class TestRevokerCascadeHook:
    """TokenRevoker.revoke() post-revoke cascade behaviour."""

    @pytest.mark.asyncio
    async def test_revoke_triggers_cascade_hook(self) -> None:
        record = MagicMock()
        record.agent_id = "agent-parent"
        record.token_id = "tok-1"
        record.role_name = "role-1"
        record.role_id = "rid-1"
        record.parent_agent_id = "root"
        record.revoked_at = None

        store = AsyncMock()
        store.get.return_value = record

        cascade = AsyncMock()
        revoker = TokenRevoker(
            secrets_manager=MagicMock(),
            token_store=store,
        )
        revoker.set_cascade_hook(cascade)
        await revoker.revoke("agent-parent")

        cascade.assert_awaited_once_with("agent-parent")

    @pytest.mark.asyncio
    async def test_revoke_skips_cascade_when_already_revoked(self) -> None:
        record = MagicMock()
        record.revoked_at = MagicMock()

        store = AsyncMock()
        store.get.return_value = record

        cascade = AsyncMock()
        revoker = TokenRevoker(
            secrets_manager=MagicMock(),
            token_store=store,
        )
        revoker.set_cascade_hook(cascade)
        await revoker.revoke("agent-x")

        cascade.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoke_skips_cascade_when_no_record(self) -> None:
        store = AsyncMock()
        store.get.return_value = None

        cascade = AsyncMock()
        revoker = TokenRevoker(
            secrets_manager=MagicMock(),
            token_store=store,
        )
        revoker.set_cascade_hook(cascade)
        await revoker.revoke("agent-missing")

        cascade.assert_not_called()

    @pytest.mark.asyncio
    async def test_cascade_hook_failure_does_not_crash_revoke(self) -> None:
        record = MagicMock()
        record.agent_id = "agent-p"
        record.token_id = "tok-p"
        record.role_name = "role-p"
        record.role_id = "rid-p"
        record.parent_agent_id = "root"
        record.revoked_at = None

        store = AsyncMock()
        store.get.return_value = record

        cascade = AsyncMock()
        cascade.side_effect = RuntimeError("cascade boom")
        revoker = TokenRevoker(
            secrets_manager=MagicMock(),
            token_store=store,
        )
        revoker.set_cascade_hook(cascade)
        await revoker.revoke("agent-p")
        store.revoke.assert_awaited_once_with("tok-p")


class TestBuildStsReaper:
    """daemon._build_sts_reaper factory behaviour."""

    def test_factory_returns_reaper_with_audit(self) -> None:
        from general_ludd.daemon import _build_sts_reaper

        session_factory = MagicMock()
        secrets_resolver = MagicMock()
        reaper = _build_sts_reaper(
            session_factory=session_factory,
            secrets_resolver=secrets_resolver,
        )
        assert isinstance(reaper, TokenReaper)
        assert reaper._audit_pipeline is not None

    def test_factory_wires_cascade_hook(self) -> None:
        from general_ludd.daemon import _build_sts_reaper

        reaper = _build_sts_reaper(
            session_factory=MagicMock(),
            secrets_resolver=MagicMock(),
        )
        assert reaper._revoker._cascade_hook is not None
