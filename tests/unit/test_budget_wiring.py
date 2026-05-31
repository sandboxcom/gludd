"""Unit tests for Feature #8: Budget Caps Wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_harness.controllers.budget import RunBudgetGuard
from agentic_harness.event_loop.loop import EventLoop
from agentic_harness.schemas.todo import (
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
        assert validate_transition(
            TodoStatus.AWAITING_RESULT, TodoStatus.BUDGET_EXCEEDED
        )

    def test_reviewing_return_to_budget_exceeded(self):
        assert validate_transition(
            TodoStatus.REVIEWING_RETURN, TodoStatus.BUDGET_EXCEEDED
        )

    def test_backlog_cannot_transition_to_budget_exceeded(self):
        assert not validate_transition(
            TodoStatus.BACKLOG, TodoStatus.BUDGET_EXCEEDED
        )

    def test_complete_cannot_transition_to_budget_exceeded(self):
        assert not validate_transition(
            TodoStatus.COMPLETE, TodoStatus.BUDGET_EXCEEDED
        )

    def test_cancelled_cannot_transition_to_budget_exceeded(self):
        assert not validate_transition(
            TodoStatus.CANCELLED, TodoStatus.BUDGET_EXCEEDED
        )

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
        from agentic_harness.schemas.task_return import TaskReturn, TaskReturnStatus

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
        from agentic_harness.schemas.task_return import TaskReturn, TaskReturnStatus

        tr = TaskReturn(
            return_id="RET-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        loop._tick_state["claimed_returns"] = [tr]

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
        from agentic_harness.models.gateway import ModelGateway, ModelProfile
        from agentic_harness.models.provider_registry import ProviderRegistry

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
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            budget_guard=guard,
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

        expected_cost = 100 * 0.01 + 50 * 0.03
        assert resp.cost_estimate == pytest.approx(expected_cost)
        assert guard.get_total_spend() == pytest.approx(expected_cost)

    def test_gateway_without_budget_guard_still_works(self):
        from agentic_harness.models.gateway import ModelGateway, ModelProfile
        from agentic_harness.models.provider_registry import ProviderRegistry

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        profile = ModelProfile(
            model_profile_id="gpt4_noguard",
            enabled=True,
            provider="openai",
            model_name="gpt-4",
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
