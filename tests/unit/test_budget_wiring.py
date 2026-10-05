"""Unit tests for Feature #8: Budget Caps Wiring.

Also covers the BudgetManager-gated dispatch executor pattern (#49 daily +
per-todo caps) in TestBudgetManagerGatedExecutor at the bottom of this file.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.controllers.budget_manager import BudgetManager
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import (
    Todo,
    TodoStatus,
    validate_transition,
)


class TestBudgetExceededStatus:
    def test_budget_exceeded_exists_in_enum(self):
        assert hasattr(TodoStatus, "BUDGET_EXCEEDED")
        assert TodoStatus.BUDGET_EXCEEDED == "budget_exceeded"

    def test_budget_exceeded_value(self):
        assert TodoStatus.BUDGET_EXCEEDED.value == "budget_exceeded"

    def test_budget_exceeded_is_str_enum(self):
        assert isinstance(TodoStatus.BUDGET_EXCEEDED, str)


class TestBudgetExceededTransitions:
    def test_active_to_budget_exceeded(self):
        assert validate_transition(TodoStatus.ACTIVE, TodoStatus.BUDGET_EXCEEDED)

    def test_awaiting_result_to_budget_exceeded(self):
        assert validate_transition(TodoStatus.AWAITING_RESULT, TodoStatus.BUDGET_EXCEEDED)

    def test_reviewing_return_to_budget_exceeded(self):
        assert validate_transition(TodoStatus.REVIEWING_RETURN, TodoStatus.BUDGET_EXCEEDED)

    def test_backlog_cannot_transition_to_budget_exceeded(self):
        assert not validate_transition(TodoStatus.BACKLOG, TodoStatus.BUDGET_EXCEEDED)

    def test_complete_cannot_transition_to_budget_exceeded(self):
        assert not validate_transition(TodoStatus.COMPLETE, TodoStatus.BUDGET_EXCEEDED)

    def test_cancelled_cannot_transition_to_budget_exceeded(self):
        assert not validate_transition(TodoStatus.CANCELLED, TodoStatus.BUDGET_EXCEEDED)

    def test_todo_transition_to_budget_exceeded_from_active(self):
        todo = Todo(title="budget test", status=TodoStatus.ACTIVE)
        todo.transition_to(TodoStatus.BUDGET_EXCEEDED)
        assert todo.status == TodoStatus.BUDGET_EXCEEDED

    def test_todo_transition_to_budget_exceeded_from_awaiting_result(self):
        todo = Todo(title="budget test", status=TodoStatus.AWAITING_RESULT)
        todo.transition_to(TodoStatus.BUDGET_EXCEEDED)
        assert todo.status == TodoStatus.BUDGET_EXCEEDED

    def test_todo_transition_to_budget_exceeded_from_reviewing_return(self):
        todo = Todo(title="budget test", status=TodoStatus.REVIEWING_RETURN)
        todo.transition_to(TodoStatus.BUDGET_EXCEEDED)
        assert todo.status == TodoStatus.BUDGET_EXCEEDED

    def test_todo_invalid_transition_to_budget_exceeded(self):
        todo = Todo(title="budget test", status=TodoStatus.BACKLOG)
        with pytest.raises(ValueError, match="Invalid transition"):
            todo.transition_to(TodoStatus.BUDGET_EXCEEDED)


class TestEventLoopBudgetGuard:
    def _make_loop(self, budget_guard=None):
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        http_client = AsyncMock()
        todo_repo = AsyncMock()
        task_return_repo = AsyncMock()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
            task_return_repo=task_return_repo,
            budget_guard=budget_guard,
        )
        return loop, {
            "session": session,
            "http_client": http_client,
            "todo_repo": todo_repo,
            "task_return_repo": task_return_repo,
        }

    @pytest.mark.asyncio
    async def test_execute_dispatch_skipped_when_budget_exceeded(self):
        guard = RunBudgetGuard(run_budget_usd=0.0)
        guard.record_spend(1.0)

        loop, mocks = self._make_loop(budget_guard=guard)
        todo = Todo(
            title="test task",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            resource_profile="low_resource",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]

        await loop._phase_dispatch_execute_jobs()

        mocks["http_client"].post.assert_not_called()
        assert loop._tick_metrics["todos_dispatched"] == 0

    @pytest.mark.asyncio
    async def test_execute_dispatch_proceeds_when_budget_ok(self):
        guard = RunBudgetGuard(run_budget_usd=100.0)

        loop, mocks = self._make_loop(budget_guard=guard)
        todo = Todo(
            title="test task",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            resource_profile="low_resource",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]

        await loop._phase_dispatch_execute_jobs()

        mocks["http_client"].post.assert_called_once()
        assert loop._tick_metrics["todos_dispatched"] == 1

    @pytest.mark.asyncio
    async def test_return_review_dispatch_skipped_when_budget_exceeded(self):
        guard = RunBudgetGuard(run_budget_usd=0.0)
        guard.record_spend(1.0)

        loop, mocks = self._make_loop(budget_guard=guard)
        from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus

        tr = TaskReturn(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        loop._tick_state["claimed_returns"] = [tr]

        await loop._phase_dispatch_return_review_jobs()

        mocks["http_client"].post.assert_not_called()
        assert loop._tick_metrics["returns_reviewed"] == 0

    @pytest.mark.asyncio
    async def test_return_review_dispatch_proceeds_when_budget_ok(self):
        guard = RunBudgetGuard(run_budget_usd=100.0)

        loop, mocks = self._make_loop(budget_guard=guard)
        from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus

        tr = TaskReturn(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        loop._tick_state["claimed_returns"] = [tr]
        mocks["http_client"].post.return_value = MagicMock(status_code=202)

        await loop._phase_dispatch_return_review_jobs()

        mocks["http_client"].post.assert_called_once()
        assert loop._tick_metrics["returns_reviewed"] == 1

    @pytest.mark.asyncio
    async def test_no_budget_guard_dispatches_normally(self):
        loop, mocks = self._make_loop(budget_guard=None)
        todo = Todo(
            title="test task",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            resource_profile="low_resource",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]

        await loop._phase_dispatch_execute_jobs()

        mocks["http_client"].post.assert_called_once()


class TestModelGatewayBudgetTracking:
    def test_gateway_records_spend_via_budget_guard(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        guard = RunBudgetGuard(run_budget_usd=100.0)
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        profile = ModelProfile(
            model_profile_id="gpt4_budget",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            cost_per_input_token=0.01,
            cost_per_output_token=0.03,
            # Server-side budget re-estimation (D-21) prices the call at up to
            # max_output_tokens (default 8000) * cost_per_output_token (0.03)
            # = ~240 USD worst case, so run_budget_usd must comfortably exceed
            # that or the per-profile cap (gateway.py check_budget, api_metered
            # defaults True) rejects the call before usage metadata can be
            # recorded. This mirrors the sibling regression test in
            # test_model_gateway.py (TestModelGatewayUsageTracking).
            run_budget_usd=10000.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            budget_guard=guard,
            billing_clock=lambda: datetime.datetime(
                2026,
                8,
                2,
                12,
                tzinfo=datetime.UTC,
            ),
        )

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="result",
            usage_metadata={"input_tokens": 100, "output_tokens": 50},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model("gpt4_budget", [{"role": "user", "content": "hi"}])

        expected_cost = (100 * 0.01 + 50 * 0.03) * 0.75  # off-peak multiplier
        assert resp.cost_estimate == pytest.approx(expected_cost)
        assert guard.get_total_spend() == pytest.approx(expected_cost)

    def test_gateway_without_budget_guard_still_works(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        profile = ModelProfile(
            model_profile_id="gpt4_noguard",
            enabled=True,
            provider="openai",
            model_name="gpt-4",
            cost_per_input_token=0.01,
            cost_per_output_token=0.03,
            max_output_tokens=1000,
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
        )

        FakeChatModel = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model("gpt4_noguard", [{"role": "user", "content": "hi"}])

        assert resp.content == "ok"


# Sentinel returned by the BudgetManager gate when a budget check defers.
_BM_DEFERRED = "deferred:budget_exhausted"


def _make_budget_gated_executor(
    *,
    executor,
    budget,
    todo_id,
    projected_cost_usd,
    realised_cost_usd=None,
):
    """Build the BudgetManager gating pattern around an async executor.

    Pattern (the daily + per-todo analogue of make_spend_guarded_executor):

      1. per-todo ceiling check  -> defer if refused
      2. daily cap check         -> defer if refused (sticky pause)
      3. run the executor
      4. record the REALISED cost (defaults to the projection) on both the
         per-todo and daily ledgers via record_spend.
    """
    charged = projected_cost_usd if realised_cost_usd is None else realised_cost_usd

    async def _gated(*args, **kwargs):
        todo_check = budget.check_todo_budget(todo_id, projected_cost_usd)
        if not todo_check["allowed"]:
            return _BM_DEFERRED
        daily_check = budget.check_daily_budget_reserved(todo_id, projected_cost_usd)
        if not daily_check["allowed"]:
            return _BM_DEFERRED
        result = await executor(*args, **kwargs)
        budget.record_spend(todo_id, charged)
        return result

    return _gated


class TestBudgetManagerGatedExecutor:
    """#49 — BudgetManager daily/per-todo caps gate the dispatch executor.

    Distinct from RunBudgetGuard (above): exercises
    controllers/budget_manager.BudgetManager (check_daily_budget /
    check_todo_budget / record_spend / get_status) and its sticky kill switch.
    """

    @pytest.mark.asyncio
    async def test_daily_cap_trips_then_stays_paused(self):
        # Daily cap 1.0; first call projects 0.8 (fits), records realised 0.8.
        budget = BudgetManager(daily_limit_usd=1.0)
        executor = AsyncMock(return_value="ran")

        guarded = _make_budget_gated_executor(
            executor=executor,
            budget=budget,
            todo_id="todo-1",
            projected_cost_usd=0.8,
        )

        assert await guarded() == "ran"
        executor.assert_awaited_once()
        status = budget.get_status()
        assert status["daily_spend"] == pytest.approx(0.8)
        assert status["paused"] is False

        # Second call: 0.8 + 0.8 = 1.6 > 1.0 -> trips the sticky pause.
        executor.reset_mock()
        assert await guarded() == _BM_DEFERRED
        executor.assert_not_awaited()
        assert budget.get_status()["paused"] is True

        # Sticky: even a tiny projection is refused while paused.
        cheap = _make_budget_gated_executor(
            executor=executor,
            budget=budget,
            todo_id="todo-1",
            projected_cost_usd=0.0001,
        )
        assert await cheap() == _BM_DEFERRED
        executor.assert_not_awaited()
        assert budget.get_status()["paused"] is True

    @pytest.mark.asyncio
    async def test_records_realised_not_projected_cost(self):
        """The gate records the REALISED cost, which can differ from the projection."""
        budget = BudgetManager(daily_limit_usd=10.0)
        executor = AsyncMock(return_value="ran")

        guarded = _make_budget_gated_executor(
            executor=executor,
            budget=budget,
            todo_id="todo-r",
            projected_cost_usd=2.0,  # admitted on projection
            realised_cost_usd=0.5,  # but actually cost less
        )
        assert await guarded() == "ran"
        assert budget.get_status()["daily_spend"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_per_todo_ceiling_defers_with_daily_headroom(self):
        # Generous daily budget, tight per-todo ceiling of 0.05.
        budget = BudgetManager(daily_limit_usd=100.0, per_todo_limit_usd=0.05)
        executor = AsyncMock(return_value="ran")

        guarded = _make_budget_gated_executor(
            executor=executor,
            budget=budget,
            todo_id="todo-x",
            projected_cost_usd=0.10,  # exceeds the per-todo ceiling
        )

        assert await guarded() == _BM_DEFERRED
        executor.assert_not_awaited()
        # Per-todo refusal must not charge the daily ledger nor pause.
        status = budget.get_status()
        assert status["daily_spend"] == pytest.approx(0.0)
        assert status["paused"] is False

    @pytest.mark.asyncio
    async def test_per_todo_accumulates_to_ceiling(self):
        """Repeated small charges on one todo trip its own ceiling, not the daily cap."""
        budget = BudgetManager(daily_limit_usd=100.0, per_todo_limit_usd=0.05)
        executor = AsyncMock(return_value="ran")

        guarded = _make_budget_gated_executor(
            executor=executor,
            budget=budget,
            todo_id="todo-y",
            projected_cost_usd=0.03,
        )
        assert await guarded() == "ran"  # 0.03 fits
        executor.reset_mock()
        # 0.03 + 0.03 = 0.06 > 0.05 ceiling -> second call defers.
        assert await guarded() == _BM_DEFERRED
        executor.assert_not_awaited()
        status = budget.get_status()
        assert status["daily_spend"] == pytest.approx(0.03)
        assert status["paused"] is False

    @pytest.mark.asyncio
    async def test_get_status_reflects_paused_after_daily_breach(self):
        budget = BudgetManager(daily_limit_usd=0.10)
        executor = AsyncMock(return_value="ran")

        guarded = _make_budget_gated_executor(
            executor=executor,
            budget=budget,
            todo_id="todo-z",
            projected_cost_usd=0.5,  # 0.5 > 0.10 -> immediate daily breach
        )

        assert budget.get_status()["paused"] is False
        assert await guarded() == _BM_DEFERRED
        executor.assert_not_awaited()

        status = budget.get_status()
        assert status["paused"] is True
        assert status["daily_limit"] == pytest.approx(0.10)
        assert status["daily_spend"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_paused_clears_after_daily_window_rollover(self):
        """Regression: a sticky pause must NOT survive the daily-window reset.

        check_daily_budget previously returned early on ``if self._paused`` BEFORE
        calling _reset_daily_if_needed(), so a single breach permanently blocked
        every subsequent day (the kill switch never recovered). The fix reorders
        the reset before the paused guard.
        """
        budget = BudgetManager(daily_limit_usd=1.0)
        # Trip the sticky pause.
        assert budget.check_daily_budget(2.0)["allowed"] is False
        assert budget.get_status()["paused"] is True
        # Roll the clock past the 24h window by back-dating the window start.
        budget._daily_start -= 86_401
        # After rollover the next check must reset + allow again.
        result = budget.check_daily_budget(0.5)
        assert result["allowed"] is True
        status = budget.get_status()
        assert status["paused"] is False
        # The rollover zeroed the prior day's spend; the new 0.5 charge is all
        # that remains on the fresh window's ledger (not the pre-rollover total).
        assert status["daily_spend"] == pytest.approx(0.5)
