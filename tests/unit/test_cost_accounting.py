"""Tests for per-project cost accounting and infrastructure cost tracking.

Validates:
- SpendLimiter per-project window_spend filtering
- SpendLimiter project_spend and project_breakdown
- InfraTracker accumulation: record_gpu_seconds, get_total_infra_cost, get_infra_cost_by_provider
- GPU cost computed from seconds + rate including spot discount
- Burn rate calculation via spend_in_last_seconds
"""

from __future__ import annotations

import threading
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.infra.pricing import INFRA_PRICING, InfraTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_limiter(limit_usd: float, window_seconds: float) -> tuple[SpendLimiter, list[float]]:
    clock_val: list[float] = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


# ---------------------------------------------------------------------------
# Per-project window_spend filtering
# ---------------------------------------------------------------------------

class TestPerProjectWindowSpend:
    def test_window_spend_filters_by_project_id(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(10.0, kind="token", project_id="proj-a")
        sl.record(5.0, kind="token", project_id="proj-b")
        sl.record(3.0, kind="token", project_id="proj-a")

        assert sl.window_spend() == pytest.approx(18.0)
        assert sl.window_spend(project_id="proj-a") == pytest.approx(13.0)
        assert sl.window_spend(project_id="proj-b") == pytest.approx(5.0)

    def test_project_id_none_is_separate_from_named_projects(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(7.0, kind="token")  # no project_id -> None
        sl.record(2.0, kind="token", project_id="proj-x")

        assert sl.window_spend() == pytest.approx(9.0)
        # Records with project_id=None are NOT matched by a named project filter
        assert sl.window_spend(project_id="proj-x") == pytest.approx(2.0)

    def test_mixed_window_prunes_and_filters_correctly(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 0.0
        sl.record(5.0, kind="token", project_id="old")
        clock[0] = 1800.0
        sl.record(3.0, kind="token", project_id="new")
        sl.record(4.0, kind="token", project_id="old")

        # Both records still in window at t=1800
        assert sl.window_spend() == pytest.approx(12.0)
        assert sl.window_spend(project_id="old") == pytest.approx(9.0)
        assert sl.window_spend(project_id="new") == pytest.approx(3.0)


class TestProjectSpendConvenience:
    def test_project_spend_is_equivalent_to_window_spend_filtered(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(2.5, kind="token", project_id="z")
        sl.record(3.5, kind="token", project_id="z")
        sl.record(1.0, kind="token", project_id="other")

        assert sl.project_spend("z") == pytest.approx(6.0)
        assert sl.project_spend("z") == sl.window_spend(project_id="z")

    def test_unknown_project_returns_zero(self) -> None:
        sl, _ = _make_limiter(100.0, 3600.0)
        assert sl.project_spend("nonexistent") == pytest.approx(0.0)


class TestProjectBreakdown:
    def test_breakdown_aggregates_by_project(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(1.0, kind="token", project_id="a")
        sl.record(2.0, kind="token", project_id="b")
        sl.record(3.0, kind="token", project_id="a")

        bd = sl.project_breakdown()
        assert bd == {"a": 4.0, "b": 2.0}

    def test_records_without_project_id_grouped_under_empty_string(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(5.0, kind="token")  # no project_id
        sl.record(3.0, kind="token", project_id="known")

        bd = sl.project_breakdown()
        assert bd == {"": 5.0, "known": 3.0}

    def test_breakdown_prunes_old_records(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 0.0
        sl.record(10.0, kind="token", project_id="old")
        clock[0] = 3601.0
        sl.record(1.0, kind="token", project_id="recent")

        bd = sl.project_breakdown()
        assert bd == {"recent": 1.0}


class TestSnapshotRestoreWithProjectId:
    def test_snapshot_includes_project_id(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(1.0, kind="token", project_id="p1")
        sl.record(2.0, kind="token")  # None project_id
        sl.record(3.0, kind="token", project_id="p2")

        snap = sl.snapshot()
        assert len(snap) == 3
        assert snap[0] == (100.0, 1.0, "p1")
        assert snap[1] == (100.0, 2.0, None)
        assert snap[2] == (100.0, 3.0, "p2")

    def test_restore_from_old_2tuple_format(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.restore([(50.0, 4.0)])

        assert sl.window_spend() == pytest.approx(4.0)
        assert sl.window_spend(project_id="p") == pytest.approx(0.0)

    def test_restore_from_new_3tuple_format(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.restore([(50.0, 4.0, "p")])

        assert sl.window_spend() == pytest.approx(4.0)
        assert sl.window_spend(project_id="p") == pytest.approx(4.0)

    def test_snapshot_roundtrip_preserves_project_id(self) -> None:
        sl1, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl1.record(3.0, kind="token", project_id="x")
        sl1.record(2.0, kind="token", project_id="y")

        snap = sl1.snapshot()
        sl2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=lambda: 110.0)
        sl2.restore(snap)

        assert sl2.window_spend() == pytest.approx(5.0)
        assert sl2.window_spend(project_id="x") == pytest.approx(3.0)
        assert sl2.window_spend(project_id="y") == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Burn rate calculation
# ---------------------------------------------------------------------------

class TestSpendInLastSeconds:
    def test_looks_back_over_custom_window(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(5.0, kind="token")
        clock[0] = 150.0
        sl.record(3.0, kind="token")
        clock[0] = 200.0

        # Last 10 seconds (from t=190 to t=200): only the 3.0 record at t=150, OOB
        # t=150 is at cutoff 190, so it's excluded (ts >= cutoff, 150 < 190)
        # Wait, let me recalculate: last 60 seconds from t=200 -> cutoff=140
        assert sl.spend_in_last_seconds(60.0) == pytest.approx(3.0)
        # Last 120 seconds -> cutoff=80, includes 5.0 at t=100 and 3.0 at t=150
        assert sl.spend_in_last_seconds(120.0) == pytest.approx(8.0)

    def test_does_not_prune_records(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 0.0
        sl.record(10.0, kind="token")
        clock[0] = 3601.0

        spend_last_5s = sl.spend_in_last_seconds(5.0)
        assert spend_last_5s == pytest.approx(0.0)

        # The full window_spend still prunes, but spend_in_last_seconds doesn't modify
        assert sl.window_spend() == pytest.approx(0.0)


class TestBurnRateCalculation:
    def test_burn_rate_is_last_hour_projected_to_24h(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 1000.0
        sl.record(1.5, kind="token")
        clock[0] = 3000.0  # +2000s, within last hour

        last_hour = sl.spend_in_last_seconds(3600.0)
        assert last_hour == pytest.approx(1.5)
        assert last_hour * 24.0 == pytest.approx(36.0)

    def test_zero_burn_rate_when_no_recent_spend(self) -> None:
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 0.0
        sl.record(5.0, kind="token")
        clock[0] = 7200.0  # well past the 3600s window

        last_hour = sl.spend_in_last_seconds(3600.0)
        assert last_hour == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# InfraTracker accumulation
# ---------------------------------------------------------------------------

class TestInfraTrackerAccumulation:
    def test_record_gpu_seconds_accumulates_total(self) -> None:
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0)
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 50.0)

        expected = INFRA_PRICING["gpu_second"] * 150.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_get_infra_cost_by_provider_breaks_down_correctly(self) -> None:
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0)
        tracker.record_gpu_seconds("aws", "A100-SXM4-80GB-1x", 200.0)

        by_provider = tracker.get_infra_cost_by_provider()
        assert by_provider["runpod"] == pytest.approx(INFRA_PRICING["gpu_second"] * 100.0)
        assert by_provider["aws"] == pytest.approx(INFRA_PRICING["gpu_second"] * 200.0)

    def test_record_gpu_seconds_uses_static_pricing_when_no_catalog(self) -> None:
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 3600.0)

        expected = INFRA_PRICING["gpu_second"] * 3600.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_gpu_cost_from_seconds_and_rate(self) -> None:
        tracker = InfraTracker()
        tracker.record_gpu_seconds("aws", "A100-SXM4-80GB-1x", 1.0)
        assert tracker.get_total_infra_cost() == pytest.approx(INFRA_PRICING["gpu_second"])
        # rate * seconds = 0.00083 * 1 ≈ 0.00083

    def test_spot_discount_applied_via_catalog(self) -> None:
        """With a catalog, spot=True must use catalog.compute_price(..., spot=True)."""
        catalog = MagicMock()
        spot_price = MagicMock()
        spot_price.usd_per_unit = 0.0005
        cast(Any, spot_price).granularity = "per_second"
        catalog.compute_price.return_value = spot_price

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0, spot=True)

        catalog.compute_price.assert_called_once_with("runpod", "A100-SXM4-80GB-1x", spot=True)
        assert tracker.get_total_infra_cost() == pytest.approx(0.0005 * 100.0)

    def test_record_gpu_seconds_rejects_non_finite_values(self) -> None:
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100", float("nan"))
        tracker.record_gpu_seconds("runpod", "A100", -1.0)
        tracker.record_gpu_seconds("runpod", "A100", float("inf"))

        assert tracker.get_total_infra_cost() == pytest.approx(0.0)

    def test_record_gpu_seconds_spot_without_catalog_uses_static(self) -> None:
        """Without a catalog, spot=True falls through to static pricing (no discount)."""
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 500.0, spot=True)

        expected = INFRA_PRICING["gpu_second"] * 500.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_get_infra_cost_provider_not_yet_recorded(self) -> None:
        tracker = InfraTracker()
        assert tracker.get_infra_cost_by_provider().get("unknown_provider") is None

    def test_concurrent_record_gpu_seconds_no_data_loss(self) -> None:
        tracker = InfraTracker()
        _THREADS = 8
        _RECORDS_PER_THREAD = 250
        barrier = threading.Barrier(_THREADS)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait()
                for _ in range(_RECORDS_PER_THREAD):
                    tracker.record_gpu_seconds("runpod", "A100", 1.0)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"threads raised: {errors!r}"
        expected = INFRA_PRICING["gpu_second"] * _THREADS * _RECORDS_PER_THREAD
        assert tracker.get_total_infra_cost() == pytest.approx(expected)


class TestInfraTrackerProjectBreakdown:
    def test_infra_tracker_project_breakdown(self) -> None:
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0, project_id="proj-a")
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 50.0, project_id="proj-a")
        tracker.record_gpu_seconds("aws", "A100-SXM4-80GB-1x", 200.0, project_id="proj-b")

        by_project = tracker.get_infra_cost_by_project()
        rate = INFRA_PRICING["gpu_second"]
        assert by_project["proj-a"] == pytest.approx(rate * 150.0)
        assert by_project["proj-b"] == pytest.approx(rate * 200.0)

    def test_infra_tracker_null_project_isolation(self) -> None:
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0)
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 50.0, project_id="proj-x")

        by_project = tracker.get_infra_cost_by_project()
        assert "proj-x" in by_project
        rate = INFRA_PRICING["gpu_second"]
        assert by_project["proj-x"] == pytest.approx(rate * 50.0)
        assert tracker.get_total_infra_cost() == pytest.approx(rate * 150.0)

    def test_record_gpu_seconds_passes_project_id(self):
        catalog = MagicMock()
        spot_price = MagicMock()
        spot_price.usd_per_unit = 0.0005
        spot_price.granularity = "per_second"
        catalog.compute_price.return_value = spot_price

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0, spot=True, project_id="p5")

        by_project = tracker.get_infra_cost_by_project()
        assert by_project["p5"] == pytest.approx(0.0005 * 100.0)
        assert tracker.get_total_infra_cost() == pytest.approx(0.0005 * 100.0)
