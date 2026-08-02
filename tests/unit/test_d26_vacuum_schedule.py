"""D-26: MemoryRecord table VACUUM schedule with leader election and rate limiting."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from general_ludd.security.vacuum_schedule import (
    VacuumResult,
    VacuumScheduler,
)


class TestVacuumSchedulerShouldVacuum:
    def test_initial_vacuum_allowed(self) -> None:
        s = VacuumScheduler()
        assert s.should_vacuum() is True

    def test_rate_limit_blocks_too_soon(self) -> None:
        s = VacuumScheduler(min_interval_sec=1800)
        s._last_vacuum_epoch = time.time()
        assert s.should_vacuum() is False

    def test_rate_limit_allows_after_interval(self) -> None:
        s = VacuumScheduler(min_interval_sec=0)
        assert s.should_vacuum() is True


class TestLeaderElection:
    def test_first_acquire_wins(self) -> None:
        s = VacuumScheduler(leader_lock_timeout_sec=300)
        assert s.try_acquire_leader() is True

    def test_second_acquire_blocked(self) -> None:
        s = VacuumScheduler(leader_lock_timeout_sec=300)
        s.try_acquire_leader()
        assert s.try_acquire_leader() is False

    def test_expired_lock_acquirable(self) -> None:
        s = VacuumScheduler(leader_lock_timeout_sec=0)
        s.try_acquire_leader()
        assert s.try_acquire_leader() is True

    def test_release_resets_lock(self) -> None:
        s = VacuumScheduler(leader_lock_timeout_sec=300)
        s.try_acquire_leader()
        s.release_leader()
        assert s._leader_lock_epoch == 0.0
        assert s.try_acquire_leader() is True


class TestVacuumMemoryTable:
    def test_skips_when_rate_limited(self) -> None:
        s = VacuumScheduler(min_interval_sec=1800)
        s._last_vacuum_epoch = time.time()
        session = MagicMock()
        result = s.vacuum_memory_table(session)
        assert result.ran is False
        assert "rate-limited" in result.skipped_reason

    def test_vacuum_runs_when_eligible(self) -> None:
        s = VacuumScheduler(min_interval_sec=0)
        session = MagicMock()
        result = s.vacuum_memory_table(session)
        assert result.ran is True
        assert result.elapsed_sec >= 0
        session.execute.assert_called_once()

    def test_skips_when_leader_election_fails(self) -> None:
        s = VacuumScheduler(min_interval_sec=0, leader_lock_timeout_sec=300)
        s.try_acquire_leader()  # already held
        s._last_vacuum_epoch = 0  # not rate-limited
        session = MagicMock()
        result = s.vacuum_memory_table(session)
        assert result.ran is False
        assert "leader-election" in result.skipped_reason


class TestVacuumResult:
    def test_default_values(self) -> None:
        r = VacuumResult(ran=True, elapsed_sec=1.5)
        assert r.ran is True
        assert r.elapsed_sec == 1.5
        assert r.skipped_reason == ""


class TestVacuumSchedulerProperties:
    def test_properties(self) -> None:
        s = VacuumScheduler(min_interval_sec=100, leader_lock_timeout_sec=200)
        assert s.min_interval_sec == 100
        assert s.leader_lock_timeout_sec == 200
        assert s.last_vacuum_epoch == 0.0
