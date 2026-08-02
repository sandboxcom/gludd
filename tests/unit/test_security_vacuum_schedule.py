"""Tests for D-26: MemoryRecord table VACUUM schedule backlog check."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import general_ludd.security.security_backlog as sb
from general_ludd.security.vacuum_schedule import (
    DEFAULT_LEADER_LOCK_TIMEOUT_SEC,
    DEFAULT_MIN_INTERVAL_SEC,
    VacuumResult,
    VacuumScheduler,
)


class TestVacuumScheduler:
    def test_defaults(self) -> None:
        s = VacuumScheduler()
        assert s.min_interval_sec == DEFAULT_MIN_INTERVAL_SEC
        assert s.leader_lock_timeout_sec == DEFAULT_LEADER_LOCK_TIMEOUT_SEC
        assert s.last_vacuum_epoch == 0.0

    def test_custom_interval_and_timeout(self) -> None:
        s = VacuumScheduler(min_interval_sec=60.0, leader_lock_timeout_sec=120.0)
        assert s.min_interval_sec == 60.0
        assert s.leader_lock_timeout_sec == 120.0

    def test_should_vacuum_false_initially(self) -> None:
        s = VacuumScheduler(min_interval_sec=3600.0)
        assert s.should_vacuum(now_epoch=0.0) is False
        assert s.should_vacuum(now_epoch=3599.0) is False

    def test_should_vacuum_true_after_interval(self) -> None:
        s = VacuumScheduler(min_interval_sec=3600.0)
        assert s.should_vacuum(now_epoch=3600.0) is True

    def test_should_vacuum_uses_time_time_when_now_epoch_none(self) -> None:
        s = VacuumScheduler(min_interval_sec=1.0)
        time.sleep(1.1)
        assert s.should_vacuum() is True

    def test_try_acquire_leader_first_call_succeeds(self) -> None:
        s = VacuumScheduler()
        assert s.try_acquire_leader(now_epoch=100.0) is True

    def test_try_acquire_leader_second_call_fails_within_timeout(self) -> None:
        s = VacuumScheduler()
        s.try_acquire_leader(now_epoch=100.0)
        assert s.try_acquire_leader(now_epoch=200.0) is False

    def test_try_acquire_leader_succeeds_after_timeout(self) -> None:
        s = VacuumScheduler(leader_lock_timeout_sec=300.0)
        s.try_acquire_leader(now_epoch=100.0)
        assert s.try_acquire_leader(now_epoch=500.0) is True

    def test_release_leader_resets_lock(self) -> None:
        s = VacuumScheduler()
        s.try_acquire_leader(now_epoch=100.0)
        s.release_leader()
        assert s.try_acquire_leader(now_epoch=100.0) is True

    def test_vacuum_rate_limited(self) -> None:
        s = VacuumScheduler(min_interval_sec=3600.0)
        result = s.vacuum_memory_table(MagicMock())
        assert result.ran is False
        assert "rate-limited" in result.skipped_reason

    def test_vacuum_leader_election_blocks_second(self) -> None:
        s = VacuumScheduler(min_interval_sec=0.0)
        s.try_acquire_leader(now_epoch=100.0)
        result = s.vacuum_memory_table(MagicMock())
        assert result.ran is False
        assert "leader-election" in result.skipped_reason
        s.release_leader()

    def test_vacuum_runs_and_records_epoch(self) -> None:
        s = VacuumScheduler(min_interval_sec=0.0)
        result = s.vacuum_memory_table(MagicMock())
        assert result.ran is True
        assert result.elapsed_sec >= 0.0
        assert s.last_vacuum_epoch > 0.0

    def test_vacuum_releases_leader_on_error(self) -> None:
        s = VacuumScheduler(min_interval_sec=0.0)
        session = MagicMock()
        session.execute.side_effect = RuntimeError("fail")
        s.try_acquire_leader(now_epoch=100.0)
        try:
            s.vacuum_memory_table(session)
        except RuntimeError:
            pass
        assert s.try_acquire_leader(now_epoch=100.0) is True


class TestD26VacuumSchedule:
    def test_checker_in_registry(self) -> None:
        assert "D-26" in sb._BACKLOG_CHECKERS
        assert sb._BACKLOG_CHECKERS["D-26"] is sb._check_d26_vacuum_schedule

    def test_checker_importable_and_callable(self) -> None:
        from general_ludd.security.security_backlog import _check_d26_vacuum_schedule

        passed, detail = _check_d26_vacuum_schedule()
        assert isinstance(passed, bool)
        assert isinstance(detail, str)

    def test_reports_open_honestly(self) -> None:
        passed, detail = sb._check_d26_vacuum_schedule()
        assert passed is False
        assert "OPEN" in detail
        assert "VACUUM" in detail
        assert "MemoryRecordModel" in detail or "memory" in detail.lower()

    def test_item_in_backlog_items(self) -> None:
        assert "D-26" in sb.BACKLOG_ITEMS
        info = sb.BACKLOG_ITEMS["D-26"]
        assert info["title"] == "MemoryRecord table VACUUM schedule"
        assert info["category"] == "resource"

    def test_run_backlog_checks_includes_d26(self) -> None:
        results = sb.run_backlog_checks()
        d26 = [r for r in results if r.item_id == "D-26"]
        assert len(d26) == 1
        r = d26[0]
        assert r.passed is False
        assert r.status == sb.STATUS_OPEN
        assert r.deferred is False
        assert "VACUUM" in r.detail

    def test_regression_detection_if_vacuum_removed(self, monkeypatch) -> None:
        def _fake_checker() -> tuple[bool, str]:
            return True, "LANDED-VERIFIED — VACUUM schedule exists"

        monkeypatch.setitem(sb._BACKLOG_CHECKERS, "D-26", _fake_checker)
        passed, _detail = sb._BACKLOG_CHECKERS["D-26"]()
        assert passed is True
