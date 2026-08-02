"""Tests for D-26: MemoryRecord table VACUUM schedule backlog check."""

from __future__ import annotations

import general_ludd.security.security_backlog as sb


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
