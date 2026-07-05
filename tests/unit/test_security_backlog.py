"""Tests for D-07 through D-47 security backlog stub implementation."""

from __future__ import annotations

from general_ludd.security.security_backlog import (
    BACKLOG_ITEMS,
    SecurityBacklogResult,
    run_backlog_checks,
)


class TestSecurityBacklogResult:
    def test_fields(self) -> None:
        r = SecurityBacklogResult(
            item_id="D-07",
            title="input validation",
            passed=True,
            detail="done",
            deferred=False,
        )
        assert r.item_id == "D-07"
        assert r.title == "input validation"
        assert r.passed is True
        assert r.detail == "done"
        assert r.deferred is False

    def test_deferred_defaults_false(self) -> None:
        r = SecurityBacklogResult(item_id="D-08", title="test", passed=True)
        assert r.deferred is False
        assert r.detail == ""


class TestBacklogItems:
    def test_has_correct_count(self) -> None:
        assert len(BACKLOG_ITEMS) == 24

    def test_all_have_title(self) -> None:
        for item_id, info in BACKLOG_ITEMS.items():
            assert "title" in info, f"{item_id} missing title"
            assert info["title"], f"{item_id} empty title"

    def test_all_have_category(self) -> None:
        for item_id, info in BACKLOG_ITEMS.items():
            assert "category" in info, f"{item_id} missing category"

    def test_known_categories(self) -> None:
        valid = {"input", "dos", "ssrf", "secret", "audit", "cleanup", "resource", "sandbox"}
        for item_id, info in BACKLOG_ITEMS.items():
            assert info["category"] in valid, f"{item_id} has unknown category {info['category']!r}"


class TestRunBacklogChecks:
    def test_returns_all_items(self) -> None:
        results = run_backlog_checks()
        assert len(results) == len(BACKLOG_ITEMS)
        result_ids = {r.item_id for r in results}
        assert result_ids == set(BACKLOG_ITEMS)

    def test_all_pass_by_default(self) -> None:
        results = run_backlog_checks()
        for r in results:
            assert r.passed is True, f"{r.item_id} failed: {r.detail}"

    def test_items_without_custom_checker_are_deferred(self) -> None:
        results = run_backlog_checks()
        for r in results:
            if r.item_id not in ("D-07", "D-14", "D-17", "D-27"):
                assert r.deferred is True, f"{r.item_id} should be deferred"

    def test_d07_has_custom_checker(self) -> None:
        results = run_backlog_checks()
        d07 = next(r for r in results if r.item_id == "D-07")
        assert d07.deferred is False
        assert "deferred" in d07.detail

    def test_d14_has_custom_checker(self) -> None:
        results = run_backlog_checks()
        d14 = next(r for r in results if r.item_id == "D-14")
        assert d14.deferred is False

    def test_d17_has_custom_checker(self) -> None:
        results = run_backlog_checks()
        d17 = next(r for r in results if r.item_id == "D-17")
        assert d17.deferred is False

    def test_d27_has_custom_checker(self) -> None:
        results = run_backlog_checks()
        d27 = next(r for r in results if r.item_id == "D-27")
        assert d27.deferred is False

    def test_results_sorted_by_item_id(self) -> None:
        results = run_backlog_checks()
        ids = [r.item_id for r in results]
        assert ids == sorted(ids)
