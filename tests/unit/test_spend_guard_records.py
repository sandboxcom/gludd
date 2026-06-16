"""TDD tests for make_spend_guarded_executor recording + fail-closed (#49).

The gate wrapper used to be INERT: it checked would_exceed() but never called
record(), so the rolling window never grew and the cap could never trip. These
tests prove the wrapper now records spend through the limiter after a call, and
fails CLOSED when the cost is unknown while a cap is configured.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.daemon_wiring import make_spend_guarded_executor


class TestGuardRecordsSpend:
    @pytest.mark.asyncio
    async def test_successful_call_records_projected_cost(self) -> None:
        """After a call proceeds, the projected cost is recorded against the window."""
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0)

        async def fake_executor(task: Any) -> str:
            return "ok"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.25,
        )
        result = await guarded(object())
        assert result == "ok"
        # The cost must have been recorded — the window is no longer zero.
        assert limiter.window_spend() == pytest.approx(0.25)

    @pytest.mark.asyncio
    async def test_repeated_calls_accumulate_and_eventually_defer(self) -> None:
        """Cap actually trips after enough recorded spend — the limiter is not inert."""
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0)

        calls: list[int] = []

        async def fake_executor(task: Any) -> str:
            calls.append(1)
            return "ok"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.4,
        )
        # 0.4 + 0.4 = 0.8 <= 1.0 (two pass), third (1.2) must defer.
        assert await guarded(object()) == "ok"
        assert await guarded(object()) == "ok"
        deferred = await guarded(object())
        assert "deferred" in str(deferred).lower()
        assert len(calls) == 2
        assert limiter.window_spend() == pytest.approx(0.8)


class TestGuardFailsClosedOnUnknownCost:
    @pytest.mark.asyncio
    async def test_unknown_cost_with_cap_defers(self) -> None:
        """projected_cost_usd=None with a cap configured -> fail CLOSED (defer)."""
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0)

        called: list[int] = []

        async def fake_executor(task: Any) -> str:
            called.append(1)
            return "ok"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=None,
        )
        result = await guarded(object())
        assert called == []  # executor must NOT run
        assert "deferred" in str(result).lower()

    @pytest.mark.asyncio
    async def test_unknown_cost_without_cap_proceeds(self) -> None:
        """No cap configured (limit<=0) -> unknown cost may proceed."""
        limiter = SpendLimiter(limit_usd=0.0, window_seconds=3600.0)

        called: list[int] = []

        async def fake_executor(task: Any) -> str:
            called.append(1)
            return "ok"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=None,
        )
        result = await guarded(object())
        assert called == [1]
        assert result == "ok"
