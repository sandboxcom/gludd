"""Deep edge-case tests for SpendLimiter.

Covers reserve/commit/release lifecycle, project_breakdown,
spend_in_last_seconds, window_spend project_id filter, restore edge cases
(non-finite timestamps, mixed 2/3-tuple, duplicates after clamping),
record() non-numeric guards, would_exceed non-numeric inputs,
mark_flushed edge values, and _min_next_ts monotonicity.
"""

from __future__ import annotations

import threading
import time
from typing import cast

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter


def _make_limiter(limit_usd: float, window_seconds: float) -> tuple[SpendLimiter, list[float]]:
    clock_val: list[float] = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


# ---------------------------------------------------------------------------
# reserve / commit / release lifecycle
# ---------------------------------------------------------------------------


class TestReserveCommitRelease:
    def test_reserve_returns_token_and_increases_spend(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        token = sl.reserve(5.0)
        assert token is not None
        assert sl.window_spend() == pytest.approx(5.0)

    def test_reserve_fails_when_would_exceed_cap(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(9.0, kind="token")
        token = sl.reserve(5.0)
        assert token is None
        assert sl.window_spend() == pytest.approx(9.0)

    def test_reserve_rejects_non_numeric_cost(self) -> None:
        sl, _ = _make_limiter(100.0, 3600.0)
        assert sl.reserve("abc") is None  # type: ignore[arg-type]
        assert sl.reserve(None) is None  # type: ignore[arg-type]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_reserve_rejects_zero_or_negative_cost(self) -> None:
        sl, _ = _make_limiter(100.0, 3600.0)
        assert sl.reserve(0.0) is None
        assert sl.reserve(-5.0) is None
        assert sl.reserve(-0.0) is None
        assert sl.window_spend() == pytest.approx(0.0)

    def test_reserve_rejects_non_finite_cost(self) -> None:
        sl, _ = _make_limiter(100.0, 3600.0)
        assert sl.reserve(float("nan")) is None
        assert sl.reserve(float("inf")) is None
        assert sl.reserve(float("-inf")) is None
        assert sl.window_spend() == pytest.approx(0.0)

    def test_commit_updates_actual_cost(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        token = sl.reserve(5.0)
        assert token is not None
        assert sl.commit(token, 3.0, kind="token") is True
        assert sl.window_spend() == pytest.approx(3.0)

    def test_commit_unknown_token_returns_false(self) -> None:
        sl, _ = _make_limiter(100.0, 3600.0)
        assert sl.commit("nonexistent", 5.0, kind="token") is False
        assert sl.window_spend() == pytest.approx(0.0)

    def test_commit_twice_on_same_token_fails_second(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        token = sl.reserve(5.0)
        assert token is not None
        assert sl.commit(token, 3.0, kind="token") is True
        assert sl.commit(token, 1.0, kind="token") is False
        assert sl.window_spend() == pytest.approx(3.0)

    def test_release_removes_reserved_spend(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        token = sl.reserve(5.0)
        assert token is not None
        assert sl.window_spend() == pytest.approx(5.0)
        assert sl.release(token) is True
        assert sl.window_spend() == pytest.approx(0.0)

    def test_release_unknown_token_returns_false(self) -> None:
        sl, _ = _make_limiter(100.0, 3600.0)
        assert sl.release("nonexistent") is False

    def test_release_after_commit_returns_false(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        token = sl.reserve(5.0)
        assert token is not None
        assert sl.commit(token, 3.0, kind="token") is True
        assert sl.release(token) is False

    def test_commit_after_release_returns_false(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        token = sl.reserve(5.0)
        assert token is not None
        assert sl.release(token) is True
        assert sl.commit(token, 3.0, kind="token") is False

    def test_reserve_commit_release_interleaved(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        t1 = sl.reserve(5.0)
        t2 = sl.reserve(7.0)
        t3 = sl.reserve(3.0)
        assert t1 is not None and t2 is not None and t3 is not None
        assert sl.window_spend() == pytest.approx(15.0)
        assert sl.release(t2) is True
        assert sl.window_spend() == pytest.approx(8.0)
        assert sl.commit(t1, 4.0, kind="token") is True
        assert sl.window_spend() == pytest.approx(7.0)
        assert sl.commit(t3, 2.5, kind="token") is True
        assert sl.window_spend() == pytest.approx(6.5)

    def test_commit_preserves_project_id(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        token = sl.reserve(5.0)
        assert token is not None
        assert sl.commit(token, 4.0, kind="token", project_id="proj-x") is True
        snap = sl.snapshot()
        assert len(snap) == 1
        assert snap[0][2] == "proj-x"


# ---------------------------------------------------------------------------
# record() non-numeric / type guards
# ---------------------------------------------------------------------------


class TestRecordTypeGuards:
    def test_record_string_cost_is_noop(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record("abc", kind="token")  # type: ignore[arg-type]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_record_none_cost_is_noop(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(None, kind="token")  # type: ignore[arg-type]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_record_bool_is_numeric_so_recorded(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(True, kind="token")  # type: ignore[arg-type]
        assert sl.window_spend() == pytest.approx(1.0)

    def test_record_list_cost_is_noop(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record([1.0], kind="token")  # type: ignore[arg-type]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_record_very_large_cost_works(self) -> None:
        sl, clock = _make_limiter(1e30, 3600.0)
        clock[0] = 1.0
        sl.record(1e20, kind="token")
        assert sl.window_spend() == pytest.approx(1e20)

    def test_record_very_small_cost_works(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(1e-15, kind="token")
        assert sl.window_spend() == pytest.approx(1e-15)

    def test_record_zero_cost_many_times(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        for _ in range(1000):
            sl.record(0.0, kind="token")
        assert sl.window_spend() == pytest.approx(0.0)

    def test_record_with_at_none_advances_min_next_ts(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 100.0
        sl.record(1.0, kind="token")  # ts=100, min_next_ts=100
        clock[0] = 50.0  # clock went backwards
        sl.record(2.0, kind="token")  # at=None, uses max(50, 100) = 100
        snap = sl.snapshot()
        assert len(snap) == 2
        assert snap[0][0] == pytest.approx(100.0)
        assert snap[1][0] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# would_exceed deep edge cases
# ---------------------------------------------------------------------------


class TestWouldExceedDeep:
    def test_would_exceed_string_returns_true(self) -> None:
        sl, _ = _make_limiter(10.0, 3600.0)
        assert sl.would_exceed("abc") is True  # type: ignore[arg-type]

    def test_would_exceed_none_returns_true(self) -> None:
        sl, _ = _make_limiter(10.0, 3600.0)
        assert sl.would_exceed(None) is True  # type: ignore[arg-type]

    def test_would_exceed_list_returns_true(self) -> None:
        sl, _ = _make_limiter(10.0, 3600.0)
        assert sl.would_exceed([5.0]) is True  # type: ignore[arg-type]

    def test_would_exceed_negative_cost_at_limit(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(10.0, kind="token")
        assert sl.would_exceed(-5.0) is False

    def test_would_exceed_zero_of_zero_limit(self) -> None:
        sl, _ = _make_limiter(0.0, 3600.0)
        assert sl.would_exceed(0.0) is False

    def test_would_exceed_positive_of_zero_limit(self) -> None:
        sl, _ = _make_limiter(0.0, 3600.0)
        assert sl.would_exceed(0.01) is True

    def test_would_exceed_saturating_addition(self) -> None:
        sl, clock = _make_limiter(1e308, 3600.0)
        clock[0] = 1.0
        sl.record(1e308, kind="token")
        assert sl.would_exceed(1e308) is True


# ---------------------------------------------------------------------------
# try_charge deep edge cases
# ---------------------------------------------------------------------------


class TestTryChargeDeep:
    def test_try_charge_zero_cost_recorded(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        assert sl.try_charge(0.0, kind="token") is True
        assert sl.window_spend() == pytest.approx(0.0)

    def test_try_charge_with_explicit_at(self) -> None:
        sl, _ = _make_limiter(10.0, 3600.0)
        assert sl.try_charge(2.0, kind="token", at=50.0) is True
        assert sl.window_spend(now=50.0) == pytest.approx(2.0)
        assert sl.window_spend(now=49.0) == pytest.approx(2.0)

    def test_try_charge_unknown_cost_negative_limit(self) -> None:
        sl, _ = _make_limiter(-1.0, 3600.0)
        assert sl.cap_configured is False
        assert sl.try_charge(None, kind="token") is True

    def test_try_charge_unknown_cost_zero_limit(self) -> None:
        sl, _ = _make_limiter(0.0, 3600.0)
        assert sl.cap_configured is False
        assert sl.try_charge(None, kind="token") is True

    def test_try_charge_unknown_cost_positive_limit(self) -> None:
        sl, _ = _make_limiter(0.01, 3600.0)
        assert sl.cap_configured is True
        assert sl.try_charge(None, kind="token") is False


# ---------------------------------------------------------------------------
# project_breakdown
# ---------------------------------------------------------------------------


class TestProjectBreakdown:
    def test_breakdown_empty_limiter(self) -> None:
        sl, _ = _make_limiter(10.0, 3600.0)
        assert sl.project_breakdown() == {}

    def test_breakdown_single_project(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(5.0, kind="token", project_id="proj-a")
        sl.record(3.0, kind="infra", project_id="proj-a")
        bd = sl.project_breakdown()
        assert bd == {"proj-a": pytest.approx(8.0)}

    def test_breakdown_multiple_projects(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(5.0, kind="token", project_id="proj-a")
        sl.record(3.0, kind="infra", project_id="proj-b")
        sl.record(2.0, kind="token", project_id="proj-a")
        bd = sl.project_breakdown()
        assert bd["proj-a"] == pytest.approx(7.0)
        assert bd["proj-b"] == pytest.approx(3.0)

    def test_breakdown_none_project_id(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(5.0, kind="token")
        sl.record(3.0, kind="infra", project_id="proj-a")
        bd = sl.project_breakdown()
        assert bd[""] == pytest.approx(5.0)
        assert bd["proj-a"] == pytest.approx(3.0)

    def test_breakdown_with_pruning(self) -> None:
        sl, clock = _make_limiter(100.0, 60.0)
        clock[0] = 0.0
        sl.record(10.0, kind="token", project_id="old")
        clock[0] = 100.0
        sl.record(5.0, kind="token", project_id="new")
        bd = sl.project_breakdown(now=100.0)
        assert "old" not in bd
        assert bd["new"] == pytest.approx(5.0)

    def test_breakdown_uses_clock_when_now_is_none(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(3.0, kind="token", project_id="p1")
        bd = sl.project_breakdown()
        assert bd["p1"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# spend_in_last_seconds
# ---------------------------------------------------------------------------


class TestSpendInLastSeconds:
    def test_zero_seconds_includes_records_at_exact_now(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 10.0
        sl.record(5.0, kind="token")
        assert sl.spend_in_last_seconds(0.0) == pytest.approx(5.0)

    def test_very_large_seconds_captures_all(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 10.0
        sl.record(5.0, kind="token")
        assert sl.spend_in_last_seconds(1e9) == pytest.approx(5.0)

    def test_mixed_old_and_new(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 0.0
        sl.record(1.0, kind="token")
        clock[0] = 50.0
        sl.record(2.0, kind="infra")
        clock[0] = 100.0
        sl.record(3.0, kind="token")
        assert sl.spend_in_last_seconds(30.0, now=100.0) == pytest.approx(3.0)
        assert sl.spend_in_last_seconds(51.0, now=100.0) == pytest.approx(5.0)
        assert sl.spend_in_last_seconds(101.0, now=100.0) == pytest.approx(6.0)

    def test_uses_clock_when_now_is_none(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 10.0
        sl.record(3.0, kind="token")
        assert sl.spend_in_last_seconds(60.0) == pytest.approx(3.0)

    def test_does_not_prune(self) -> None:
        sl, clock = _make_limiter(10.0, 10.0)
        clock[0] = 0.0
        sl.record(5.0, kind="token")
        clock[0] = 100.0
        sl.spend_in_last_seconds(5.0, now=100.0)
        assert len(sl.snapshot()) == 1


# ---------------------------------------------------------------------------
# window_spend with project_id filter
# ---------------------------------------------------------------------------


class TestWindowSpendProjectFilter:
    def test_filter_to_matching_project(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(5.0, kind="token", project_id="proj-a")
        sl.record(3.0, kind="infra", project_id="proj-b")
        assert sl.window_spend(project_id="proj-a") == pytest.approx(5.0)

    def test_filter_to_nonexistent_project(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(5.0, kind="token", project_id="proj-a")
        assert sl.window_spend(project_id="proj-z") == pytest.approx(0.0)

    def test_filter_none_matches_records_with_no_project_id(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(2.0, kind="token")
        sl.record(3.0, kind="infra")
        assert sl.window_spend(project_id=None) == pytest.approx(5.0)

    def test_filter_with_pruning(self) -> None:
        sl, clock = _make_limiter(100.0, 60.0)
        clock[0] = 0.0
        sl.record(10.0, kind="token", project_id="proj-a")
        clock[0] = 100.0
        sl.record(5.0, kind="token", project_id="proj-a")
        assert sl.window_spend(now=100.0, project_id="proj-a") == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# restore deep edge cases
# ---------------------------------------------------------------------------


class TestRestoreDeep:
    def test_restore_non_finite_timestamp_dropped(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([(float("nan"), 5.0)])
        assert sl.window_spend() == pytest.approx(0.0)

    def test_restore_inf_timestamp_dropped(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([(float("inf"), 5.0), (float("-inf"), 3.0)])
        assert sl.window_spend() == pytest.approx(0.0)

    def test_restore_mixed_2_and_3_tuple(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore(cast(list[tuple[float, float, str | None]], [(10.0, 2.0), (10.0, 3.0, "proj-a")]))
        assert sl.window_spend() == pytest.approx(5.0)
        snap = sl.snapshot()
        costs_only = [(ts, c, pid) for ts, c, pid in snap]
        assert (10.0, 2.0, None) in costs_only
        assert (10.0, 3.0, "proj-a") in costs_only

    def test_restore_duplicate_exact_records_skipped(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([(10.0, 5.0, "p"), (10.0, 5.0, "p")])
        assert sl.window_spend() == pytest.approx(5.0)

    def test_restore_duplicate_after_clamping_skipped(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.restore([(200.0, 5.0), (300.0, 5.0)])
        assert sl.window_spend() == pytest.approx(5.0)

    def test_restore_non_string_project_id_ignored(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([(10.0, 5.0, 123)])  # type: ignore[list-item]
        snap = sl.snapshot()
        assert snap[0][2] is None

    def test_restore_non_numeric_types_in_tuple(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([("abc", 5.0)])  # type: ignore[list-item]
        assert sl.window_spend() == pytest.approx(0.0)
        sl.restore([(10.0, "xyz")])  # type: ignore[list-item]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_restore_wrong_length_tuple_skipped(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([(10.0,), (10.0, 5.0, "p", "extra")])  # type: ignore[list-item]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_restore_negative_timestamp_not_dropped_by_timestamp_guard(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([(-100.0, 5.0)])
        assert sl.window_spend() == pytest.approx(5.0)

    def test_restore_then_new_charge_does_not_duplicate(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.restore([(90.0, 5.0)])
        sl.record(5.0, kind="token")
        assert sl.window_spend() == pytest.approx(10.0)
        assert len(sl.snapshot()) == 2

    def test_restore_empty_string_project_id_preserved(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.restore([(10.0, 5.0, "")])
        snap = sl.snapshot()
        assert snap[0][2] == ""


# ---------------------------------------------------------------------------
# mark_flushed edge cases
# ---------------------------------------------------------------------------


class TestMarkFlushedDeep:
    def test_mark_flushed_zero_is_noop(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")  # seq=1
        sl.mark_flushed(0)
        assert len(sl.unflushed_records()) == 1

    def test_mark_flushed_negative_is_noop(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")  # seq=1
        sl.mark_flushed(-1)
        assert len(sl.unflushed_records()) == 1

    def test_mark_flushed_far_future(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")  # seq=1
        sl.record(2.0, kind="token")  # seq=2
        sl.mark_flushed(9_999_999)
        assert sl.unflushed_records() == []


# ---------------------------------------------------------------------------
# cap_configured boundary
# ---------------------------------------------------------------------------


class TestCapConfigured:
    def test_positive_limit_is_configured(self) -> None:
        sl, _ = _make_limiter(0.01, 3600.0)
        assert sl.cap_configured is True

    def test_zero_limit_is_unconfigured(self) -> None:
        sl, _ = _make_limiter(0.0, 3600.0)
        assert sl.cap_configured is False

    def test_negative_limit_is_unconfigured(self) -> None:
        sl, _ = _make_limiter(-50.0, 3600.0)
        assert sl.cap_configured is False

    def test_very_small_positive_is_configured(self) -> None:
        sl, _ = _make_limiter(1e-15, 3600.0)
        assert sl.cap_configured is True


# ---------------------------------------------------------------------------
# default clock
# ---------------------------------------------------------------------------


class TestDefaultClockDeep:
    def test_default_clock_uses_real_time(self) -> None:
        sl = SpendLimiter(limit_usd=100.0, window_seconds=3600.0)
        before = time.monotonic()
        sl.record(1.0, kind="token")
        after = time.monotonic()
        snap = sl.snapshot()
        assert len(snap) == 1
        assert before - 1.0 <= snap[0][0] <= after + 1.0


# ---------------------------------------------------------------------------
# concurrent reserve/commit/release
# ---------------------------------------------------------------------------


class TestConcurrentReserveCommitRelease:
    def test_concurrent_reserve_then_commit_all(self) -> None:
        sl, clock = _make_limiter(1000.0, 3600.0)
        clock[0] = 1.0
        tokens: list[str] = []
        tokens_lock = threading.Lock()
        start = threading.Barrier(10)

        def worker() -> None:
            start.wait()
            t = sl.reserve(1.0)
            if t is not None:
                with tokens_lock:
                    tokens.append(t)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(tokens) == 10
        total_before = sl.window_spend()
        assert total_before == pytest.approx(10.0)
        for token in tokens:
            assert sl.commit(token, 0.5, kind="token") is True
        assert sl.window_spend() == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# snapshot / project_id round-trip
# ---------------------------------------------------------------------------


class TestSnapshotProjectIdRoundTrip:
    def test_snapshot_preserves_project_ids(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl.record(1.0, kind="token", project_id="a")
        sl.record(2.0, kind="infra", project_id="b")
        sl.record(3.0, kind="token")
        snap = sl.snapshot()
        assert snap == [
            (10.0, 1.0, "a"),
            (10.0, 2.0, "b"),
            (10.0, 3.0, None),
        ]

    def test_snapshot_restore_round_trips_project_ids(self) -> None:
        sl1, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 10.0
        sl1.record(1.0, kind="token", project_id="a")
        sl1.record(2.0, kind="infra", project_id="b")
        snap = sl1.snapshot()

        sl2, clock2 = _make_limiter(100.0, 3600.0)
        clock2[0] = 10.0
        sl2.restore(snap)
        assert sl2.window_spend() == pytest.approx(3.0)
        assert sl2.project_spend("a") == pytest.approx(1.0)
        assert sl2.project_spend("b") == pytest.approx(2.0)
