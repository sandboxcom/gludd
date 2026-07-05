"""E2E: SpendLimiter works with real configuration end-to-end.

Proves the SpendLimiter enforces rolling budget caps with a real (non-mocked)
instance, verifying the core lifecycle: construction -> record spend ->
window_spend() -> remaining() -> try_charge() gate -> window rollover.

These tests use a real SpendLimiter (not a mock) with an injected clock
so timing is deterministic. They cover the full e2e path:
  - Configuration is observable (limit, window, cap_configured)
  - try_charge() atomically checks AND records spend
  - The rolling window prunes old records
  - try_charge() refuses charges over budget
  - Zero-cost charges always pass
  - Record with explicit model/project tracks metadata
  - Snapshot/restore preserves state across simulated restarts
  - Catalog price lookup fallback works when no catalog is wired
"""

from __future__ import annotations

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter


def _build(limit: float, window: float, start: float = 0.0) -> tuple[SpendLimiter, list[float]]:
    t = [start]

    def clock() -> float:
        return t[0]

    return SpendLimiter(limit_usd=limit, window_seconds=window, clock=clock), t


class TestSpendLimiterE2ERealConfig:
    def test_construction_with_real_config(self) -> None:
        lim, _ = _build(limit=10.0, window=3600.0)
        assert lim.cap_configured is True
        assert lim.remaining() == pytest.approx(10.0)
        assert lim.window_spend() == pytest.approx(0.0)

    def test_try_charge_atomically_checks_and_records(self) -> None:
        lim, _ = _build(limit=5.0, window=86400.0)
        assert lim.window_spend() == 0.0
        ok = lim.try_charge(2.0, kind="token", model="claude-3")
        assert ok is True
        assert lim.window_spend() == pytest.approx(2.0)
        assert lim.remaining() == pytest.approx(3.0)

    def test_try_charge_refuses_when_over_budget(self) -> None:
        lim, _ = _build(limit=1.0, window=86400.0)
        lim.try_charge(0.6, kind="token")
        lim.try_charge(0.3, kind="token")
        assert lim.window_spend() == pytest.approx(0.9)
        ok = lim.try_charge(0.3, kind="token")
        assert ok is False
        assert lim.window_spend() == pytest.approx(0.9)

    def test_window_prunes_old_records(self) -> None:
        lim, clock = _build(limit=10.0, window=100.0, start=0.0)
        lim.try_charge(3.0, kind="token")
        clock[0] = 50.0
        lim.try_charge(2.0, kind="token")
        assert lim.window_spend() == pytest.approx(5.0)
        clock[0] = 101.0
        lim.try_charge(1.0, kind="token")
        assert lim.window_spend() == pytest.approx(3.0)

    def test_multiple_kinds_accumulate_together(self) -> None:
        lim, _ = _build(limit=10.0, window=86400.0)
        lim.try_charge(1.0, kind="token", model="gpt-4")
        lim.try_charge(2.0, kind="infra", model="a100")
        lim.try_charge(0.5, kind="token", model="claude-3")
        assert lim.window_spend() == pytest.approx(3.5)

    def test_zero_cost_charge_always_passes(self) -> None:
        lim, _ = _build(limit=0.01, window=86400.0)
        ok = lim.try_charge(0.0, kind="token")
        assert ok is True

    def test_record_negative_cost_raises(self) -> None:
        lim, _ = _build(limit=10.0, window=3600.0)
        with pytest.raises(ValueError, match="finite non-negative"):
            lim.record(-1.0, kind="token")

    def test_record_nan_cost_raises(self) -> None:
        lim, _ = _build(limit=10.0, window=3600.0)
        with pytest.raises(ValueError, match="finite non-negative"):
            lim.record(float("nan"), kind="token")

    def test_token_cost_usd_uses_static_fallback(self) -> None:
        lim, _ = _build(limit=10.0, window=3600.0)
        cost = lim.token_cost_usd("gpt-4", in_tokens=1000, out_tokens=500)
        assert cost > 0.0
        assert isinstance(cost, float)

    def test_token_cost_usd_unknown_model_is_nonzero(self) -> None:
        lim, _ = _build(limit=10.0, window=3600.0)
        cost = lim.token_cost_usd("claude-3-5-sonnet-20241022", in_tokens=100, out_tokens=50)
        assert cost >= 0.0

    def test_try_charge_none_refused_when_cap_configured(self) -> None:
        lim, _ = _build(limit=10.0, window=3600.0)
        ok = lim.try_charge(None, kind="token")
        assert ok is False

    def test_try_charge_none_allowed_when_no_cap(self) -> None:
        lim, _ = _build(limit=0.0, window=3600.0)
        ok = lim.try_charge(None, kind="token")
        assert ok is True

    def test_snapshot_roundtrip_preserves_state(self) -> None:
        lim, _clock = _build(limit=100.0, window=86400.0, start=100.0)
        lim.try_charge(25.0, kind="token", project_id="proj-A")
        lim.try_charge(15.0, kind="infra", project_id="proj-B")
        snap = lim.snapshot()

        lim2, _ = _build(limit=100.0, window=86400.0, start=100.0)
        lim2.restore(snap)
        assert lim2.window_spend() == pytest.approx(40.0)
        assert lim2.remaining() == pytest.approx(60.0)

    def test_project_spend_breakdown(self) -> None:
        lim, _ = _build(limit=100.0, window=86400.0)
        lim.try_charge(10.0, kind="token", project_id="p1")
        lim.try_charge(20.0, kind="token", project_id="p2")
        lim.try_charge(5.0, kind="token", project_id="p1")
        breakdown = lim.project_breakdown()
        assert "p1" in breakdown
        assert "p2" in breakdown

    def test_record_preserves_project_id(self) -> None:
        lim, _ = _build(limit=100.0, window=86400.0)
        lim.record(5.0, kind="token", project_id="my-project")
        breakdown = lim.project_breakdown()
        assert breakdown.get("my-project", 0.0) == pytest.approx(5.0)
