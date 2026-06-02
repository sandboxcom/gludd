from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.benchmark import RoutingDecision, TaskType
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


class TestAdaptiveRouterWiring:
    @pytest.mark.asyncio
    async def test_no_adaptive_router_uses_static_prompt(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="test",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            prompt_profile="default",
            model_profile="gpt4",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        await loop._dispatch_execute_job(todo)
        call_kwargs = mocks["http_client"].post.call_args[1]["json"]
        assert call_kwargs["prompt_profile"] == "default"
        assert call_kwargs["model_profile"] == "gpt4"

    @pytest.mark.asyncio
    async def test_adaptive_router_overrides_profile(self):
        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id="aider_v2",
            selected_model_profile_id="claude3",
            composite_score=0.95,
            estimated_cost_usd=0.01,
            sample_count=10,
            fallback=False,
            reason="best_historical_score",
        )
        loop, mocks = _make_loop(adaptive_router=router)
        todo = Todo(
            title="test",
            todo_id="TODO-002",
            status=TodoStatus.ACTIVE,
            prompt_profile="default",
            model_profile="gpt4",
            work_type="code",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        await loop._dispatch_execute_job(todo)
        router.route.assert_called_once()
        call_kwargs = mocks["http_client"].post.call_args[1]["json"]
        assert call_kwargs["prompt_profile"] == "aider_v2"
        assert call_kwargs["model_profile"] == "claude3"

    @pytest.mark.asyncio
    async def test_adaptive_router_fallback_uses_todo_defaults(self):
        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id=None,
            selected_model_profile_id="default",
            composite_score=0.0,
            estimated_cost_usd=0.0,
            sample_count=0,
            fallback=True,
            reason="insufficient_historical_data",
        )
        loop, mocks = _make_loop(adaptive_router=router)
        todo = Todo(
            title="test",
            todo_id="TODO-003",
            status=TodoStatus.ACTIVE,
            prompt_profile="static_profile",
            model_profile="my_model",
            work_type="refactor",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        await loop._dispatch_execute_job(todo)
        call_kwargs = mocks["http_client"].post.call_args[1]["json"]
        assert call_kwargs["prompt_profile"] == "static_profile"
        assert call_kwargs["model_profile"] == "my_model"

    @pytest.mark.asyncio
    async def test_resolve_adaptive_prompt_classifies_task_type(self):
        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.8,
            estimated_cost_usd=0.05,
            sample_count=5,
            fallback=False,
            reason="best_historical_score",
        )
        loop, _ = _make_loop(adaptive_router=router)
        todo = Todo(
            title="refactor module",
            todo_id="TODO-004",
            status=TodoStatus.ACTIVE,
            work_type="refactor",
        )
        _prompt_id, _model_id, _decision = await loop._resolve_adaptive_prompt(todo)
        router.route.assert_called_once()
        call_kwargs = router.route.call_args[1]
        assert call_kwargs["task_type"] == TaskType.REFACTOR

    @pytest.mark.asyncio
    async def test_resolve_adaptive_prompt_unknown_work_type_defaults_feature(self):
        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id=None,
            selected_model_profile_id="default",
            composite_score=0.0,
            estimated_cost_usd=0.0,
            sample_count=0,
            fallback=True,
            reason="insufficient_historical_data",
        )
        loop, _ = _make_loop(adaptive_router=router)
        todo = MagicMock()
        todo.work_type = "weird_type"
        await loop._resolve_adaptive_prompt(todo)
        call_kwargs = router.route.call_args[1]
        assert call_kwargs["task_type"] == TaskType.FEATURE

    @pytest.mark.asyncio
    async def test_resolve_adaptive_prompt_no_router_returns_none(self):
        loop, _ = _make_loop()
        todo = Todo(title="test", todo_id="TODO-006", status=TodoStatus.ACTIVE)
        result = await loop._resolve_adaptive_prompt(todo)
        assert result == (None, None, None)

    @pytest.mark.asyncio
    async def test_adaptive_router_used_in_dispatch_with_runner(self):
        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id="aider",
            selected_model_profile_id="sonnet",
            composite_score=0.9,
            estimated_cost_usd=0.02,
            sample_count=8,
            fallback=False,
            reason="best_historical_score",
        )
        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/job"}
        loop, _ = _make_loop(adaptive_router=router, runner=runner)
        todo = Todo(
            title="test",
            todo_id="TODO-007",
            status=TodoStatus.ACTIVE,
            work_type="test",
        )
        await loop._dispatch_execute_job(todo)
        write_vars_call = runner.write_vars.call_args
        job_vars = write_vars_call[1]["job_vars"]
        assert job_vars["prompt_profile"] == "aider"
        assert job_vars["model_profile"] == "sonnet"
