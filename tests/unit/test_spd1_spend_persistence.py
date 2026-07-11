"""SPD-1: Spend persistence — restart-survival flush path.

The in-memory :class:`SpendLimiter` already enforces the live rolling-window
cap.  SPD-1 closes the restart-survival gap: charges recorded in memory must be
periodically flushed to ``spend_records`` (via ``SpendRepository.add()``) so a
daemon restart rehydrates a non-empty window instead of resetting the cap to
zero.

Coverage:
  1. try_charge increments the internal ``_seq`` counter.
  2. unflushed_records() returns records recorded past the last flush watermark.
  3. mark_flushed(seq) advances the watermark.
  4. restore() from persisted records seeds the watermark (no re-flush).
  5. the EventLoop flush phase writes unflushed records through
     SpendRepository.add().
  6. restart-survival e2e: flush → new limiter instance → restore →
     rehydrated total matches the flushed amount.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.daemon import _restore_persisted_spend
from general_ludd.db.models import Base
from general_ludd.db.repository import SpendRepository
from general_ludd.event_loop.loop import EventLoop


@pytest_asyncio.fixture
async def db_factory() -> Any:
    """Real in-memory sqlite DB with the full schema, shared via StaticPool."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _fixed_clock() -> Any:
    """A clock pinned to a fixed wall-clock time (deterministic, in-window)."""
    now = time.time()
    return lambda: now


def _make_flush_loop(spend_limiter: SpendLimiter, factory: Any) -> EventLoop:
    """Minimal EventLoop wired only for the spend-flush phase under test."""
    loop = EventLoop(
        config={"spend_persist_interval_ticks": 1},
        daemon_state={},
        session=factory,
        spend_limiter=spend_limiter,
    )
    return loop


class TestSeqAndWatermark:
    def test_try_charge_increments_seq(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=_fixed_clock())
        assert limiter._seq == 0
        assert limiter.try_charge(1.0, kind="token") is True
        assert limiter._seq == 1
        assert limiter.try_charge(2.0, kind="token") is True
        assert limiter._seq == 2

    def test_unflushed_records_returns_new_since_last_flush(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=_fixed_clock())
        limiter.try_charge(1.0, kind="token")
        limiter.try_charge(2.0, kind="token")
        recs = limiter.unflushed_records()
        assert [r[0] for r in recs] == [1, 2]
        assert [r[2] for r in recs] == [1.0, 2.0]
        limiter.mark_flushed(1)
        recs_after = limiter.unflushed_records()
        assert [r[0] for r in recs_after] == [2]

    def test_mark_flushed_updates_watermark(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=_fixed_clock())
        limiter.try_charge(1.0, kind="token")
        limiter.try_charge(2.0, kind="token")
        assert len(limiter.unflushed_records()) == 2
        limiter.mark_flushed(2)
        assert limiter.unflushed_records() == []
        limiter.mark_flushed(1)
        assert limiter.unflushed_records() == []

    def test_restore_seeds_watermark(self) -> None:
        """restore()d records are already persisted → not re-flushable."""
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=_fixed_clock())
        now = time.time()
        limiter.restore([(now, 5.0, None), (now, 3.0, None)])
        assert limiter.unflushed_records() == []
        assert limiter.try_charge(1.0, kind="token") is True
        pending = limiter.unflushed_records()
        assert len(pending) == 1
        assert pending[0][2] == 1.0


class TestFlushPhase:
    @pytest.mark.asyncio
    async def test_flush_phase_writes_to_repository(self, db_factory: Any) -> None:
        factory = db_factory
        clock = _fixed_clock()
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=clock)
        limiter.try_charge(1.5, kind="token", project_id="proj-a")
        limiter.try_charge(2.5, kind="token", project_id="proj-b")

        loop = _make_flush_loop(limiter, factory)
        loop._total_ticks = 1
        await loop._phase_flush_spend_ledger()

        since = clock() - 3600
        async with factory() as session:
            rows = await SpendRepository(session).list_since(since)
        costs = sorted(r.cost_usd for r in rows)
        assert costs == [1.5, 2.5]
        assert limiter.unflushed_records() == []

    @pytest.mark.asyncio
    async def test_flush_phase_disabled_when_interval_non_positive(self, db_factory: Any) -> None:
        factory = db_factory
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=_fixed_clock())
        limiter.try_charge(1.0, kind="token")
        loop = EventLoop(
            config={"spend_persist_interval_ticks": 0},
            daemon_state={},
            session=factory,
            spend_limiter=limiter,
        )
        loop._total_ticks = 1
        await loop._phase_flush_spend_ledger()
        async with factory() as session:
            rows = await SpendRepository(session).list_since(0.0)
        assert rows == []
        assert len(limiter.unflushed_records()) == 1


class TestRestartSurvival:
    @pytest.mark.asyncio
    async def test_restart_survival_e2e(self, db_factory: Any) -> None:
        """flush → simulate restart (new limiter + restore) → total matches."""
        factory = db_factory
        clock = _fixed_clock()

        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=clock)
        limiter.try_charge(4.0, kind="token")
        limiter.try_charge(6.0, kind="token")
        flushed_total = limiter.window_spend()
        assert flushed_total == pytest.approx(10.0)

        loop = _make_flush_loop(limiter, factory)
        loop._total_ticks = 1
        await loop._phase_flush_spend_ledger()

        restored = SpendLimiter(limit_usd=100.0, window_seconds=3600, clock=clock)
        assert restored.window_spend() == pytest.approx(0.0)

        await _restore_persisted_spend(restored, factory, window_seconds=3600)

        assert restored.window_spend() == pytest.approx(flushed_total)
        assert restored.unflushed_records() == []
