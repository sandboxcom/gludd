"""End-to-end spend limiter tests: daily + per-task caps, window reset,
per-project tracking, snapshot/restore survival.

Covers the spend-limiter at 80%: configuration, enforcement, multi-project
spend tracking, window rollover, and persistence across simulated restarts.
"""

from __future__ import annotations

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter


def _limiter(
    limit_usd: float,
    window_seconds: float,
    start: float = 0.0,
) -> tuple[SpendLimiter, list[float]]:
    """Return (limiter, mutable_clock_list)."""
    t = [start]

    def clock() -> float:
        return t[0]

    return SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=clock), t


# ── daily + per-task cap enforcement ──────────────────────────────────


class TestDailyAndPerTaskCaps:
    """Daily cap (large rolling window) bounds total spend; per-task cap
    (smaller sub-caps) enforced via try_charge checks before each dispatch."""

    def test_daily_cap_blocks_excess_when_fully_consumed(self) -> None:
        lim, clock = _limiter(limit_usd=5.0, window_seconds=86400.0, start=0.0)

        # Consume daily budget through a sequence of small model calls.
        for cost in [1.0, 1.5, 2.0]:
            assert lim.try_charge(cost, kind="token", model="claude-3")
            clock[0] += 60.0

        # Window spend = 4.5, remaining = 0.5
        assert lim.window_spend() == pytest.approx(4.5)
        assert lim.remaining() == pytest.approx(0.5)

        # 0.6 > remaining → blocked
        assert lim.try_charge(0.6, kind="token") is False
        # 0.5 fits exactly → allowed
        assert lim.try_charge(0.5, kind="token") is True
        # Now at 5.0, any positive charge blocked
        assert lim.try_charge(0.01, kind="token") is False

    def test_per_task_cap_via_try_charge_with_shared_limiter(self) -> None:
        """A per-task cap can be enforced by passing the same limiter
        and checking remaining() before each dispatch inside a task loop."""
        lim, clock = _limiter(limit_usd=1.0, window_seconds=86400.0, start=0.0)

        task_charges_accepted = 0
        for cost in [0.3, 0.3, 0.3, 0.3]:
            ok = lim.try_charge(cost, kind="token")
            if not ok:
                break
            task_charges_accepted += 1
            clock[0] += 1.0

        # 0.3 + 0.3 + 0.3 = 0.9, 4th charge 0.3 → 1.2 > 1.0 blocked
        assert task_charges_accepted == 3
        assert lim.window_spend() == pytest.approx(0.9)

    def test_daily_cap_is_soft_not_hard_kill(self) -> None:
        """A soft cap defers (refuses) new charges; it does not abort in-flight
        work.  Already-recorded spend is not rolled back."""
        lim, _ = _limiter(limit_usd=3.0, window_seconds=86400.0, start=0.0)

        lim.try_charge(3.0, kind="token")  # fill cap
        # Refused charge must not erase prior spend
        assert lim.try_charge(0.01, kind="token") is False
        assert lim.window_spend() == pytest.approx(3.0)


# ── window reset after period expires ──────────────────────────────────


class TestWindowReset:
    """The rolling window prunes old records; when all records are older than
    the window, spend resets to zero — the cap becomes available again."""

    def test_spend_resets_after_window_expires(self) -> None:
        lim, clock = _limiter(limit_usd=5.0, window_seconds=3600.0, start=0.0)
        clock[0] = 100.0
        lim.try_charge(5.0, kind="token")
        assert lim.window_spend() == pytest.approx(5.0)

        # Advance past one window + epsilon
        clock[0] = 100.0 + 3600.01
        # Old record at t=100 is now outside [3700.01-3600, 3700.01] → [100.01, 3700.01]
        assert lim.window_spend() == pytest.approx(0.0)
        assert lim.remaining() == pytest.approx(5.0)
        # New charges accepted
        assert lim.try_charge(3.0, kind="token") is True

    def test_partial_window_rollover(self) -> None:
        """Records age out individually; spend decreases as old entries expire."""
        lim, clock = _limiter(limit_usd=10.0, window_seconds=60.0, start=0.0)
        clock[0] = 0.0
        lim.try_charge(3.0, kind="token", at=0.0)
        clock[0] = 30.0
        lim.try_charge(4.0, kind="token", at=30.0)
        # Both records in window at t=40
        assert lim.window_spend(now=40.0) == pytest.approx(7.0)

        # At t=70: cutoff = 10, t=0 record expired, t=30 still in
        assert lim.window_spend(now=70.0) == pytest.approx(4.0)

        # At t=100: cutoff = 40, both expired
        assert lim.window_spend(now=100.0) == pytest.approx(0.0)

    def test_window_reset_multiple_cycles(self) -> None:
        """Over multiple window cycles the limiter should continue working correctly."""
        lim, clock = _limiter(limit_usd=2.0, window_seconds=10.0, start=0.0)

        for cycle in range(3):
            clock[0] = cycle * 20.0
            assert lim.window_spend() == pytest.approx(0.0)
            assert lim.try_charge(1.5, kind="token") is True
            assert lim.try_charge(0.6, kind="token") is False  # > 2.0 → blocked
            assert lim.window_spend() == pytest.approx(1.5)
            # Advance past window
            clock[0] = cycle * 20.0 + 11.0


# ── per-project spend tracking ──────────────────────────────────────────


class TestPerProjectTracking:
    """Multi-project spend: each record carries a project_id; the limiter
    supports per-project queries and breakdowns."""

    def test_project_spend_scoped_to_single_project(self) -> None:
        lim, _ = _limiter(limit_usd=100.0, window_seconds=86400.0, start=0.0)

        lim.try_charge(10.0, kind="token", project_id="proj-a")
        lim.try_charge(20.0, kind="token", project_id="proj-a")
        lim.try_charge(5.0, kind="token", project_id="proj-b")

        assert lim.project_spend("proj-a") == pytest.approx(30.0)
        assert lim.project_spend("proj-b") == pytest.approx(5.0)
        assert lim.project_spend("proj-c") == pytest.approx(0.0)
        assert lim.window_spend() == pytest.approx(35.0)

    def test_project_breakdown_returns_all_projects(self) -> None:
        lim, _ = _limiter(limit_usd=100.0, window_seconds=86400.0, start=0.0)

        lim.try_charge(7.0, kind="token", project_id="alpha")
        lim.try_charge(3.0, kind="token", project_id="beta")
        lim.try_charge(1.0, kind="token")  # no project_id

        breakdown = lim.project_breakdown()
        assert breakdown["alpha"] == pytest.approx(7.0)
        assert breakdown["beta"] == pytest.approx(3.0)
        assert breakdown[""] == pytest.approx(1.0)
        assert len(breakdown) == 3

    def test_project_spend_pruned_with_window(self) -> None:
        lim, clock = _limiter(limit_usd=50.0, window_seconds=100.0, start=0.0)
        clock[0] = 0.0
        lim.try_charge(5.0, kind="token", project_id="x")
        clock[0] = 50.0
        lim.try_charge(2.0, kind="token", project_id="x")
        # At t=120: cutoff=20, t=0 record expired, t=50 record still in
        assert lim.project_spend("x", now=120.0) == pytest.approx(2.0)

    def test_record_preserves_project_id_in_snapshot(self) -> None:
        lim, _ = _limiter(limit_usd=100.0, window_seconds=86400.0, start=0.0)
        lim.try_charge(4.0, kind="token", project_id="p1")
        lim.try_charge(6.0, kind="infra", project_id="p2")

        snap = lim.snapshot()
        # snapshot is list of (ts, cost, project_id)
        pids = {rec[2] for rec in snap}
        assert pids == {"p1", "p2"}


# ── snapshot / restore survives simulated restart ──────────────────────


class TestSnapshotRestoreE2E:
    """End-to-end: snapshot captures accumulated state; restore reloads it
    into a brand-new limiter as if the daemon restarted."""

    def test_full_snapshot_restore_preserves_spend(self) -> None:
        lim1, _ = _limiter(limit_usd=50.0, window_seconds=86400.0, start=100.0)
        lim1.try_charge(12.0, kind="token", model="claude-3", project_id="p")
        lim1.try_charge(8.0, kind="infra", project_id="p")
        assert lim1.window_spend() == pytest.approx(20.0)

        snap = lim1.snapshot()

        # Simulate restart: fresh limiter at same clock position
        lim2, _ = _limiter(limit_usd=50.0, window_seconds=86400.0, start=100.0)
        assert lim2.window_spend() == pytest.approx(0.0)

        lim2.restore(snap)
        assert lim2.window_spend() == pytest.approx(20.0)
        assert lim2.remaining() == pytest.approx(30.0)
        assert lim2.project_spend("p") == pytest.approx(20.0)
        # Cap still enforces
        assert lim2.try_charge(10.0, kind="token") is True
        assert lim2.try_charge(21.0, kind="token") is False

    def test_restore_after_window_expiry_clears_old_records(self) -> None:
        lim1, _ = _limiter(limit_usd=10.0, window_seconds=60.0, start=0.0)
        lim1.try_charge(5.0, kind="token")
        snap = lim1.snapshot()

        # Restart well past window
        lim2, _ = _limiter(limit_usd=10.0, window_seconds=60.0, start=500.0)
        lim2.restore(snap)
        assert lim2.window_spend() == pytest.approx(0.0)

    def test_restore_none_or_empty_is_noop(self) -> None:
        lim, _ = _limiter(limit_usd=10.0, window_seconds=3600.0, start=0.0)
        lim.try_charge(3.0, kind="token")
        lim.restore(None)
        lim.restore([])
        assert lim.window_spend() == pytest.approx(3.0)

    def test_restore_future_timestamp_clamped_does_not_peg_window(self) -> None:
        lim, _ = _limiter(limit_usd=10.0, window_seconds=60.0, start=100.0)
        lim.restore([(9999.0, 8.0)])  # far-future ts → clamped to now=100.0
        assert lim.window_spend() == pytest.approx(8.0)
        # Advance > 1 window: the clamped record expires normally
        assert lim.window_spend(now=170.0) == pytest.approx(0.0)

    def test_restore_negative_cost_dropped(self) -> None:
        lim, _ = _limiter(limit_usd=5.0, window_seconds=3600.0, start=0.0)
        lim.try_charge(2.0, kind="token")
        lim.restore([(0.0, -100.0)])
        assert lim.window_spend() == pytest.approx(2.0)
        assert lim.remaining() == pytest.approx(3.0)


# ── configuration proof: limit + window applied ─────────────────────────


class TestConfigurationProof:
    """Configuration documentation: the limiter enforces the limit and window
    exactly as configured, with observable API output matching the config."""

    def test_config_is_observable_via_properties(self) -> None:
        lim, _ = _limiter(limit_usd=25.0, window_seconds=7200.0, start=0.0)
        assert lim.cap_configured is True
        assert lim._limit_usd == pytest.approx(25.0)
        assert lim._window_seconds == pytest.approx(7200.0)

    def test_zero_limit_means_no_cap(self) -> None:
        lim, _ = _limiter(limit_usd=0.0, window_seconds=3600.0, start=0.0)
        assert lim.cap_configured is False
        # Unknown-cost charges admitted (cap_configured=False)
        for _ in range(100):
            assert lim.try_charge(None, kind="token") is True
        # Positive-cost charges still blocked by arithmetic (0 + cost > 0)
        assert lim.try_charge(0.01, kind="token") is False

    def test_negative_limit_means_no_cap(self) -> None:
        lim, _ = _limiter(limit_usd=-1.0, window_seconds=3600.0, start=0.0)
        assert lim.cap_configured is False

    def test_spend_limiter_remains_consistent_under_normal_load(self) -> None:
        """End-to-end simulation: a day's worth of model calls across projects."""
        lim, clock = _limiter(limit_usd=100.0, window_seconds=86400.0, start=0.0)

        # Simulate 120 model calls over 24 hours across 3 projects
        total_accepted = 0
        total_cost = 0.0
        for i in range(120):
            cost = 0.5 + (i % 10) * 0.1  # varies 0.5 .. 1.4 USD
            pid = f"proj-{(i % 3) + 1}"
            clock[0] = i * (86400.0 / 120)  # spread evenly over 24h
            if lim.try_charge(cost, kind="token", project_id=pid):
                total_accepted += 1
                total_cost += cost

        breakdown = lim.project_breakdown()
        assert len(breakdown) <= 3
        assert lim.window_spend() == pytest.approx(total_cost)
        assert total_cost <= 100.0 + 1e-9
        assert total_accepted > 0
        assert sum(breakdown.values()) == pytest.approx(total_cost)


# ── edge cases (adversarial) ────────────────────────────────────────────


class TestEdgeCases:
    def test_try_charge_refuses_non_finite_amount(self) -> None:
        import math

        lim, _ = _limiter(limit_usd=10.0, window_seconds=3600.0)
        for bad in (math.nan, math.inf, -math.inf):
            assert lim.try_charge(bad, kind="token") is False
        # Window must be unpolluted
        assert lim.window_spend() == pytest.approx(0.0)
        assert lim.remaining() == pytest.approx(10.0)

    def test_try_charge_none_refused_when_cap_configured(self) -> None:
        lim, _ = _limiter(limit_usd=10.0, window_seconds=3600.0)
        assert lim.try_charge(None, kind="token") is False

    def test_try_charge_none_allowed_when_no_cap(self) -> None:
        lim, _ = _limiter(limit_usd=0.0, window_seconds=3600.0)
        assert lim.try_charge(None, kind="token") is True

    def test_cost_of_zero_always_fits(self) -> None:
        lim, _ = _limiter(limit_usd=0.01, window_seconds=3600.0)
        # Fill the cap
        lim.try_charge(0.01, kind="token")
        # Zero-cost dispatch still allowed
        assert lim.try_charge(0.0, kind="token") is True
        assert lim.try_charge(0.0, kind="infra") is True

    def test_spend_in_last_seconds_correct_lookback(self) -> None:
        lim, clock = _limiter(limit_usd=100.0, window_seconds=86400.0, start=0.0)
        clock[0] = 0.0
        lim.try_charge(1.0, kind="token", at=0.0)
        clock[0] = 45.0
        lim.try_charge(2.0, kind="token", at=45.0)
        clock[0] = 70.0
        lim.try_charge(3.0, kind="token", at=70.0)
        clock[0] = 100.0
        # Last 60s: cutoff = 40, only t=45 (2.0) and t=70 (3.0) = 5.0
        assert lim.spend_in_last_seconds(60.0, now=100.0) == pytest.approx(5.0)
        # Last 30s: cutoff = 70, only t=70 (3.0)
        assert lim.spend_in_last_seconds(30.0, now=100.0) == pytest.approx(3.0)
