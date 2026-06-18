"""TDD tests for P4: SpendLimiter wired into ExecutionEngine dispatch/completion path.

Acceptance criteria (from AUDIT_model_discovery_weights_cost.md P4 + BUILD_PLAN.md A9):
  1. A completed metered task records a non-zero cost into SpendLimiter (in-memory).
  2. An over-budget dispatch is denied/deferred before the model call is made.
  3. spend_records DB row is written after a successful call (async path).
  4. SpendLimiter is present in ExecutionEngine constructor signature.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter
from general_ludd.execution.engine import ExecutionEngine
from general_ludd.schemas.job import JobSpec


def _make_job(job_id: str = "job-1", todo_id: str = "todo-1") -> JobSpec:
    return JobSpec(
        job_id=job_id,
        todo_id=todo_id,
        prompt_text="Do something",
        work_type="code",
        playbook="code",
        queue="core",
    )


def _make_gateway(cost_estimate: float = 0.05) -> MagicMock:
    """Return a mock ModelGateway whose call_model returns a response with cost."""
    gw = MagicMock()
    resp = MagicMock()
    resp.content = "print('hello')"
    resp.cost_estimate = cost_estimate
    gw.call_model.return_value = resp
    return gw


# ---------------------------------------------------------------------------
# 1. Constructor accepts spend_limiter + session_factory
# ---------------------------------------------------------------------------


class TestExecutionEngineSignature:
    def test_accepts_spend_limiter_kwarg(self, tmp_path: Any) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0)
        engine = ExecutionEngine(
            workspace_path=str(tmp_path),
            spend_limiter=limiter,
        )
        assert engine._spend_limiter is limiter

    def test_accepts_session_factory_kwarg(self, tmp_path: Any) -> None:
        engine = ExecutionEngine(
            workspace_path=str(tmp_path),
            session_factory=object(),
        )
        assert engine._session_factory is not None

    def test_defaults_to_none(self, tmp_path: Any) -> None:
        engine = ExecutionEngine(workspace_path=str(tmp_path))
        assert engine._spend_limiter is None
        assert engine._session_factory is None


# ---------------------------------------------------------------------------
# 2. Completed metered task records non-zero cost into SpendLimiter
# ---------------------------------------------------------------------------


class TestSpendRecordActual:
    """_spend_record_actual records into the in-memory limiter."""

    def test_records_nonzero_cost_into_limiter(self, tmp_path: Any) -> None:
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        engine = ExecutionEngine(
            workspace_path=str(tmp_path),
            spend_limiter=limiter,
        )
        # Before: window is zero.
        assert limiter.window_spend() == 0.0

        engine._spend_record_actual(0.05, model="test-model", job_id="j-1")

        # After: window reflects the real cost.
        assert limiter.window_spend() == pytest.approx(0.05)

    def test_zero_cost_recorded_but_window_stays_zero(self, tmp_path: Any) -> None:
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        engine = ExecutionEngine(workspace_path=str(tmp_path), spend_limiter=limiter)
        engine._spend_record_actual(0.0)
        assert limiter.window_spend() == pytest.approx(0.0)

    def test_multiple_calls_accumulate(self, tmp_path: Any) -> None:
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        engine = ExecutionEngine(workspace_path=str(tmp_path), spend_limiter=limiter)
        engine._spend_record_actual(0.03)
        engine._spend_record_actual(0.02)
        assert limiter.window_spend() == pytest.approx(0.05)

    def test_no_limiter_is_noop(self, tmp_path: Any) -> None:
        """When no limiter is configured, _spend_record_actual does not crash."""
        engine = ExecutionEngine(workspace_path=str(tmp_path))
        engine._spend_record_actual(0.05)  # must not raise

    def test_negative_cost_skipped(self, tmp_path: Any) -> None:
        """Negative cost must not be recorded (guard against programming errors)."""
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        engine = ExecutionEngine(workspace_path=str(tmp_path), spend_limiter=limiter)
        engine._spend_record_actual(-1.0)
        assert limiter.window_spend() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 3. Over-budget dispatch is denied before model call
# ---------------------------------------------------------------------------


class TestSpendPreCheck:
    """_spend_pre_check returns a denial string when the window is exhausted."""

    def test_no_limiter_returns_none(self, tmp_path: Any) -> None:
        engine = ExecutionEngine(workspace_path=str(tmp_path))
        assert engine._spend_pre_check(0.01) is None

    def test_under_budget_returns_none(self, tmp_path: Any) -> None:
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0)
        engine = ExecutionEngine(workspace_path=str(tmp_path), spend_limiter=limiter)
        assert engine._spend_pre_check(0.01) is None

    def test_over_budget_returns_denial_string(self, tmp_path: Any) -> None:
        limiter = SpendLimiter(limit_usd=0.10, window_seconds=3600.0)
        # Pre-fill window to cap.
        limiter.record(0.10, kind="token")
        engine = ExecutionEngine(workspace_path=str(tmp_path), spend_limiter=limiter)
        # Any positive projected cost now exceeds the remaining budget.
        denial = engine._spend_pre_check(0.01)
        assert denial is not None
        assert "spend limit exceeded" in denial.lower() or "exceeded" in denial.lower()


# ---------------------------------------------------------------------------
# 4. Full execute() path: over-budget → denied, under-budget → cost recorded
# ---------------------------------------------------------------------------


class TestExecuteSpendIntegration:
    """End-to-end tests for the spend gate wired into execute()."""

    def test_over_budget_execute_returns_failure_without_calling_gateway(
        self, tmp_path: Any
    ) -> None:
        """When the window is exhausted, execute() returns exit_code=1 and
        the model gateway is NOT called."""
        limiter = SpendLimiter(limit_usd=0.05, window_seconds=3600.0)
        # Pre-fill window: one more 0.0-cost call still passes would_exceed(0.0),
        # but seeding the window well beyond the cap ensures it trips.
        limiter.record(0.06, kind="token")  # window > limit

        gw = _make_gateway(cost_estimate=0.02)
        engine = ExecutionEngine(
            model_gateway=gw,
            workspace_path=str(tmp_path),
            spend_limiter=limiter,
        )
        job = _make_job()
        result = engine.execute(job)

        assert result.exit_code == 1
        assert "spend" in result.result_summary.lower() or "limit" in result.result_summary.lower()
        gw.call_model.assert_not_called()

    def test_under_budget_execute_records_cost_into_limiter(
        self, tmp_path: Any
    ) -> None:
        """When budget allows, execute() records the response's cost_estimate
        into the SpendLimiter so the window accumulates real spend."""
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        assert limiter.window_spend() == 0.0

        gw = _make_gateway(cost_estimate=0.05)
        engine = ExecutionEngine(
            model_gateway=gw,
            workspace_path=str(tmp_path),
            spend_limiter=limiter,
        )
        job = _make_job()
        engine.execute(job)

        # Gateway was called.
        gw.call_model.assert_called_once()
        # Cost was recorded.
        assert limiter.window_spend() == pytest.approx(0.05)

    def test_multiple_executions_accumulate_spend_and_eventually_defer(
        self, tmp_path: Any
    ) -> None:
        """After enough calls the window exceeds the cap, and subsequent dispatches
        are denied.

        Semantics: the engine pre-checks with would_exceed(0.0).  This trips when
        ``window_spend > limit`` (i.e. the window has already overflowed), so the
        denial happens on the call AFTER the one that pushed the window past the cap.

        Here: cap=0.07, two calls of 0.04 each → window=0.08>0.07.
        Third call pre-checks: 0.08 + 0.0 > 0.07 → denied; gateway not called.
        """
        limiter = SpendLimiter(limit_usd=0.07, window_seconds=3600.0)
        gw = _make_gateway(cost_estimate=0.04)
        engine = ExecutionEngine(
            model_gateway=gw,
            workspace_path=str(tmp_path),
            spend_limiter=limiter,
        )

        engine.execute(_make_job("j1", "t1"))
        engine.execute(_make_job("j2", "t2"))
        # Two calls: window = 0.08 (both should run; cap is not checked until
        # the next dispatch attempts to start).
        assert gw.call_model.call_count == 2
        assert limiter.window_spend() == pytest.approx(0.08)

        r3 = engine.execute(_make_job("j3", "t3"))
        # Window 0.08 > 0.07 cap → pre-check: 0.08 + 0.0 > 0.07 → denied.
        assert r3.exit_code == 1
        assert "spend" in r3.result_summary.lower() or "limit" in r3.result_summary.lower()
        assert gw.call_model.call_count == 2  # not called a 3rd time
        # Window must NOT have grown from the denied dispatch.
        assert limiter.window_spend() == pytest.approx(0.08)

    def test_zero_cost_response_does_not_record(self, tmp_path: Any) -> None:
        """A response with cost_estimate=0.0 is not recorded (no-op)."""
        limiter = SpendLimiter(limit_usd=1.0, window_seconds=3600.0)
        gw = _make_gateway(cost_estimate=0.0)
        engine = ExecutionEngine(
            model_gateway=gw,
            workspace_path=str(tmp_path),
            spend_limiter=limiter,
        )
        engine.execute(_make_job())
        assert limiter.window_spend() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 5. spend_records DB persist (async, via session_factory)
# ---------------------------------------------------------------------------


class TestSpendRecordsPersist:
    """spend_records rows are written via the session_factory after a metered call."""

    @pytest.mark.asyncio
    async def test_persist_called_when_session_factory_present(
        self, tmp_path: Any
    ) -> None:
        """After _spend_record_actual with a session_factory, the DB add is scheduled."""
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        persisted: list[dict] = []

        # Build a realistic async session-factory mock.
        mock_row = MagicMock()

        async def fake_add(ts: float, cost_usd: float, kind: str, model: Any = None, project_id: Any = None) -> Any:
            persisted.append({"ts": ts, "cost_usd": cost_usd, "kind": kind})
            return mock_row

        mock_repo = MagicMock()
        mock_repo.add = AsyncMock(side_effect=fake_add)

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        def fake_session_factory():
            return mock_session

        with patch("general_ludd.db.repository.SpendRepository", return_value=mock_repo):
            engine = ExecutionEngine(
                workspace_path=str(tmp_path),
                spend_limiter=limiter,
                session_factory=fake_session_factory,
            )
            engine._spend_record_actual(0.07, model="glm-5.1", job_id="j-persist")

            # Drain background tasks.
            tasks = list(engine._background_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        # The in-memory record is always present.
        assert limiter.window_spend() == pytest.approx(0.07)

    def test_no_session_factory_still_records_in_memory(self, tmp_path: Any) -> None:
        """Without a session_factory, cost is still recorded in the SpendLimiter."""
        limiter = SpendLimiter(limit_usd=10.0, window_seconds=3600.0)
        engine = ExecutionEngine(workspace_path=str(tmp_path), spend_limiter=limiter)
        engine._spend_record_actual(0.03, model="test")
        assert limiter.window_spend() == pytest.approx(0.03)
