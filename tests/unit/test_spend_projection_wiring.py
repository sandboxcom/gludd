"""Wiring tests: make_spend_guarded_executor with a REAL projected cost (#49).

The historical bug was a projected_cost_usd of 0.0 wired everywhere: the gate
was effectively inert because a 0.0 projection NEVER trips would_exceed() and
NEVER records spend, so the rolling window could never grow and the cap could
never fire. These tests pin the corrected wiring:

  * a real, positive projection derived from pricing.token_cost_usd actually
    DEFERS when the cap is exceeded and ACCUMULATES spend across calls (the
    0.0-projection regression would never defer);
  * a single over-budget projection defers IMMEDIATELY (first call), and the
    executor is never run;
  * a None projection fails CLOSED under a configured cap.

These complement test_spend_guard_records.py / test_dispatch_wiring.py by
sourcing the projection from the real pricing helper rather than a hand-picked
literal, proving the dispatch path's projected_cost_usd is non-zero in practice.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.daemon_wiring import make_spend_guarded_executor
from general_ludd.infra.pricing import token_cost_usd

# A realistic per-call projection from the documented pricing table. Sonnet at
# 1k in / 1k out = 0.003 + 0.015 = 0.018 USD — a strictly positive cost, unlike
# the inert 0.0 default that never deferred.
_MODEL = "claude-3-5-sonnet-20241022"
_PROJECTED = token_cost_usd(_MODEL, 1000, 1000)


def test_real_projection_is_positive() -> None:
    """Guard the premise: the pricing-derived projection is strictly > 0.

    If this ever regresses to 0.0, every deferral test below would pass
    vacuously, so we assert the projection is the thing under test.
    """
    assert _PROJECTED > 0.0
    assert abs(_PROJECTED - 0.018) < 1e-9


class TestRealProjectionAccumulatesAndDefers:
    """A positive projection records spend and eventually trips the cap."""

    @pytest.mark.asyncio
    async def test_repeated_real_cost_calls_accumulate_then_defer(self) -> None:
        # Cap fits exactly two 0.018 charges (0.036) but not a third (0.054).
        limiter = SpendLimiter(limit_usd=0.04, window_seconds=3600.0)

        calls: list[int] = []

        async def fake_executor(task: Any) -> str:
            calls.append(1)
            return "ran"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=_PROJECTED,
        )

        assert await guarded(object()) == "ran"
        assert await guarded(object()) == "ran"
        # Spend accumulated across the two accepted calls (was inert at 0.0).
        assert limiter.window_spend() == pytest.approx(2 * _PROJECTED)

        # Third call would push 0.054 > 0.04 cap -> deferred, executor not run.
        deferred = await guarded(object())
        assert "deferred" in str(deferred).lower()
        assert len(calls) == 2
        # Refused charge must NOT have been recorded.
        assert limiter.window_spend() == pytest.approx(2 * _PROJECTED)

    @pytest.mark.asyncio
    async def test_zero_projection_contrast_never_defers(self) -> None:
        """Regression contrast: the old 0.0 projection never defers, even at cap.

        Documents WHY a real projection is required — with 0.0 the gate is inert
        no matter how full the window already is.
        """
        limiter = SpendLimiter(limit_usd=0.01, window_seconds=3600.0)
        limiter.record(0.01, kind="token")  # window already at the cap

        async def fake_executor(task: Any) -> str:
            return "ran"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.0,
        )
        # 0.0 projection: would_exceed is False even at the limit -> proceeds.
        assert await guarded(object()) == "ran"


class TestSingleOverBudgetProjectionDefersImmediately:
    """One over-cap projection defers on the very first call."""

    @pytest.mark.asyncio
    async def test_first_call_over_cap_defers_without_running(self) -> None:
        # Projection 0.018 already exceeds a tiny 0.005 cap on call #1.
        limiter = SpendLimiter(limit_usd=0.005, window_seconds=3600.0)

        called: list[int] = []

        async def fake_executor(task: Any) -> str:
            called.append(1)
            return "ran"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=_PROJECTED,
        )

        result = await guarded(object())
        assert "deferred" in str(result).lower()
        assert called == []  # executor never invoked
        assert limiter.window_spend() == pytest.approx(0.0)  # nothing charged


class TestNoneProjectionFailsClosedUnderCap:
    """An unknown (None) projection under a configured cap fails CLOSED."""

    @pytest.mark.asyncio
    async def test_none_projection_defers_under_cap(self) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0)

        called: list[int] = []

        async def fake_executor(task: Any) -> str:
            called.append(1)
            return "ran"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=None,
        )

        result = await guarded(object())
        assert "deferred" in str(result).lower()
        assert called == []
        # Fail-closed must not poison the window with an unknown amount.
        assert limiter.window_spend() == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_none_projection_without_cap_proceeds(self) -> None:
        """Symmetry check: with no cap (limit<=0) an unknown cost may proceed."""
        limiter = SpendLimiter(limit_usd=0.0, window_seconds=3600.0)
        assert limiter.cap_configured is False

        called: list[int] = []

        async def fake_executor(task: Any) -> str:
            called.append(1)
            return "ran"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=None,
        )
        assert await guarded(object()) == "ran"
        assert called == [1]
