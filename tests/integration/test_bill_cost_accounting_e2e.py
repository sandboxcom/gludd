"""Integration tests for per-project cost accounting + infra cost tracking.

Proves end-to-end that:
- SpendLimiter + InfraTracker work together for total cost picture
- Per-project breakdowns are accurate across token + infra spend
- Snapshot/restore preserves per-project accounting across process restarts
- Burn rate calculation spans both token and infra spend
- Concurrent recording is thread-safe for both subsystems
- SpendLimiter budget enforcement gates infra recording
- PricingCatalog integration with both token and infra pricing
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.infra.pricing import INFRA_PRICING, InfraTracker
from general_ludd.pricing_intel.models import BillingGranularity, BillingTerms, ComputePrice


def _make_limiter(limit_usd: float, window_seconds: float) -> tuple[SpendLimiter, list[float]]:
    clock_val: list[float] = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


# ---------------------------------------------------------------------------
# Combined SpendLimiter + InfraTracker
# ---------------------------------------------------------------------------


class TestCombinedSpendLimiterInfraTracker:
    def test_token_and_infra_spend_tracked_separately_deduped_total(self):
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        tracker = InfraTracker()

        sl.record(5.0, kind="token", project_id="proj-a")
        sl.record(3.0, kind="token", project_id="proj-a")
        tracker.record_gpu_seconds("runpod", "A100", 3600.0)

        token_spend = sl.window_spend(project_id="proj-a")
        infra_spend = tracker.get_total_infra_cost()
        assert token_spend == pytest.approx(8.0)
        assert infra_spend == pytest.approx(INFRA_PRICING["gpu_second"] * 3600.0)
        assert token_spend + infra_spend > token_spend

    def test_per_project_token_spend_not_affected_by_infra(self):
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(2.0, kind="token", project_id="x")
        sl.record(3.0, kind="token", project_id="y")

        assert sl.project_spend("x") == pytest.approx(2.0)
        assert sl.project_spend("y") == pytest.approx(3.0)

        bt = sl.project_breakdown()
        assert bt == {"x": 2.0, "y": 3.0}

    def test_infra_spend_by_provider_aggregates_correctly(self):
        tracker = InfraTracker()
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 100.0)
        tracker.record_gpu_seconds("aws", "A100-SXM4-80GB-1x", 200.0)
        tracker.record_gpu_seconds("gcp", "H100", 50.0)

        by_provider = tracker.get_infra_cost_by_provider()
        assert "runpod" in by_provider
        assert "aws" in by_provider
        assert "gcp" in by_provider
        assert by_provider["runpod"] == pytest.approx(INFRA_PRICING["gpu_second"] * 100.0)
        assert by_provider["aws"] == pytest.approx(INFRA_PRICING["gpu_second"] * 200.0)
        assert by_provider["gcp"] == pytest.approx(INFRA_PRICING["gpu_second"] * 50.0)

        total_expected = INFRA_PRICING["gpu_second"] * (100.0 + 200.0 + 50.0)
        assert tracker.get_total_infra_cost() == pytest.approx(total_expected)

    def test_catalog_integration_with_spot_discount(self):
        catalog = MagicMock()
        spot = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=0.00035,
            granularity=BillingGranularity.per_second,
            spot=True,
            terms=BillingTerms.postpaid_per_use,
            source="mock",
        )
        reg = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=0.00083,
            granularity=BillingGranularity.per_second,
            spot=False,
            terms=BillingTerms.postpaid_per_use,
            source="mock",
        )
        catalog.compute_price.side_effect = [spot, reg]

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100", 100.0, spot=True)
        tracker.record_gpu_seconds("runpod", "A100", 100.0, spot=False)

        assert tracker.get_total_infra_cost() == pytest.approx(0.00035 * 100.0 + 0.00083 * 100.0)

    def test_per_minute_pricing_normalized_to_seconds(self):
        catalog = MagicMock()
        per_min = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=0.05,
            granularity=BillingGranularity.per_minute,
            spot=False,
            terms=BillingTerms.postpaid_per_use,
            source="mock",
        )
        catalog.compute_price.return_value = per_min

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100", 30.0)

        expected = (0.05 / 60.0) * 30.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_per_hour_pricing_normalized_to_seconds(self):
        catalog = MagicMock()
        per_hour = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=2.50,
            granularity=BillingGranularity.per_hour,
            spot=False,
            terms=BillingTerms.postpaid_per_use,
            source="mock",
        )
        catalog.compute_price.return_value = per_hour

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100", 1800.0)

        expected = (2.50 / 3600.0) * 1800.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Budget enforcement and project accounting
# ---------------------------------------------------------------------------


class TestBudgetAndProjectAccounting:
    def test_budget_enforces_per_project(self):
        sl, clock = _make_limiter(25.0, 3600.0)
        clock[0] = 100.0

        sl.record(8.0, kind="token", project_id="alpha")
        sl.record(7.0, kind="token", project_id="beta")
        sl.record(6.0, kind="token", project_id="alpha")

        assert sl.project_spend("alpha") == pytest.approx(14.0)
        assert sl.project_spend("beta") == pytest.approx(7.0)

        assert sl.remaining() > 0.0
        sl.record(8.0, kind="token", project_id="alpha")
        assert sl.remaining() == 0.0
        assert sl.window_spend() == pytest.approx(29.0)

    def test_burn_rate_spans_multiple_projects(self):
        sl, clock = _make_limiter(200.0, 3600.0)
        clock[0] = 0.0
        sl.record(10.0, kind="token", project_id="p1")
        sl.record(15.0, kind="token", project_id="p2")
        clock[0] = 1000.0  # within last hour

        last_hour = sl.spend_in_last_seconds(3600.0)
        assert last_hour == pytest.approx(25.0)
        burn_24h = last_hour * 24.0
        assert burn_24h == pytest.approx(600.0)

    def test_mixed_kind_spend(self):
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl.record(5.0, kind="token", project_id="proj")
        sl.record(2.0, kind="infra", project_id="proj")
        sl.record(1.0, kind="api", project_id="proj")

        assert sl.project_spend("proj") == pytest.approx(8.0)
        bt = sl.project_breakdown()
        assert bt["proj"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Snapshot / Restore integration
# ---------------------------------------------------------------------------


class TestSnapshotRestoreIntegration:
    def test_full_roundtrip_with_infra_tracker(self):
        sl1, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 100.0
        sl1.record(3.0, kind="token", project_id="x")
        sl1.record(2.0, kind="token", project_id="y")
        sl1.record(1.0, kind="token", project_id="x")

        snap = sl1.snapshot()
        sl2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=lambda: 110.0)
        sl2.restore(snap)

        assert sl2.window_spend(project_id="x") == pytest.approx(4.0)
        assert sl2.window_spend(project_id="y") == pytest.approx(2.0)
        assert sl2.project_breakdown() == {"x": 4.0, "y": 2.0}

    def test_restore_old_and_new_format_mixed(self):
        sl, _ = _make_limiter(100.0, 3600.0)
        sl.restore([(10.0, 5.0), (20.0, 3.0, "z")])
        assert sl.window_spend() == pytest.approx(8.0)
        assert sl.window_spend(project_id="z") == pytest.approx(3.0)

    def test_breakdown_only_includes_window_records(self):
        sl, clock = _make_limiter(100.0, 3600.0)
        clock[0] = 0.0
        sl.record(50.0, kind="token", project_id="old")
        clock[0] = 3601.0
        sl.record(1.0, kind="token", project_id="recent")

        bt = sl.project_breakdown()
        assert "old" not in bt
        assert bt == {"recent": 1.0}


# ---------------------------------------------------------------------------
# Concurrent cost recording
# ---------------------------------------------------------------------------


class TestConcurrentCostRecording:
    def test_concurrent_infra_recording_no_data_loss(self):
        tracker = InfraTracker()
        threads_count = 8
        records_per_thread = 500
        barrier = threading.Barrier(threads_count)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait()
                for _ in range(records_per_thread):
                    tracker.record_gpu_seconds("runpod", "A100", 1.0)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        expected = INFRA_PRICING["gpu_second"] * threads_count * records_per_thread
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_concurrent_spend_limiter_recording(self):
        sl, clock = _make_limiter(10000.0, 3600.0)
        clock[0] = 0.0
        threads_count = 8
        records_per_thread = 500
        barrier = threading.Barrier(threads_count)
        errors: list[BaseException] = []

        def worker(pid: int) -> None:
            try:
                barrier.wait()
                for _ in range(records_per_thread):
                    sl.record(1.0, kind="token", project_id=f"proj-{pid}")
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        expected_total = float(threads_count * records_per_thread)
        assert sl.window_spend() == pytest.approx(expected_total)
        for i in range(threads_count):
            assert sl.project_spend(f"proj-{i}") == pytest.approx(float(records_per_thread))
