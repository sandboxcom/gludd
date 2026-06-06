"""Tests for obj01: AdaptiveRouter wired into daemon EventLoop."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


class TestAdaptiveRouterWiringInDaemon:
    def test_extended_subsystems_creates_adaptive_router_with_session_factory(self):
        from general_ludd.daemon import _get_or_create_extended_subsystems

        app = MagicMock()
        app.state._config_dir = None
        sf = AsyncMock()
        result = _get_or_create_extended_subsystems(app, session_factory=sf)
        assert "adaptive_router" in result
        router = result["adaptive_router"]
        assert router is not None
        assert router._repo is not None

    def test_extended_subsystems_no_session_factory_returns_none_router(self):
        from general_ludd.daemon import _get_or_create_extended_subsystems

        app = MagicMock()
        app.state._config_dir = None
        app.state._skill_registry = None
        app.state._metrics_collector = None
        app.state._project_manager = None
        app.state._utilization_tracker = None
        app.state._model_registry = None
        del app.state._adaptive_router
        result = _get_or_create_extended_subsystems(app, session_factory=None)
        assert result["adaptive_router"] is None

    def test_extended_subsystems_caches_adaptive_router(self):
        from general_ludd.daemon import _get_or_create_extended_subsystems

        app = MagicMock()
        app.state._config_dir = None
        sf = AsyncMock()
        result1 = _get_or_create_extended_subsystems(app, session_factory=sf)
        result2 = _get_or_create_extended_subsystems(app, session_factory=sf)
        assert result1["adaptive_router"] is result2["adaptive_router"]

    def test_event_loop_receives_adaptive_router(self):
        from general_ludd.event_loop.loop import EventLoop

        router = MagicMock()
        loop = EventLoop(
            adaptive_router=router,
        )
        assert loop._adaptive_router is router

    def test_event_loop_adaptive_router_defaults_to_none(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()
        assert loop._adaptive_router is None

    @pytest.mark.asyncio
    async def test_benchmark_repo_works_with_session_factory(self):
        from general_ludd.db.repository import BenchmarkRepository

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        session.add = MagicMock()

        sf = MagicMock(return_value=session)

        repo = BenchmarkRepository(sf)
        scores = {"completion": 1.0, "code_quality": 0.9, "instruction": 0.8, "token_efficiency": 0.7}
        row = await repo.record_result(
            model_profile_id="m1",
            task_type="bug_fix",
            scores=scores,
            success=True,
        )
        assert row is not None
        session.add.assert_called()

    @pytest.mark.asyncio
    async def test_benchmark_repo_works_with_direct_session(self):
        from general_ludd.db.repository import BenchmarkRepository

        session = AsyncMock(spec=AsyncSession)
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        session.add = MagicMock()

        repo = BenchmarkRepository(session)
        scores = {"completion": 0.5}
        row = await repo.record_result(
            model_profile_id="m2",
            task_type="feature",
            scores=scores,
            success=False,
        )
        assert row is not None

    @pytest.mark.asyncio
    async def test_benchmark_repo_no_session_raises(self):
        from general_ludd.db.repository import BenchmarkRepository

        repo = BenchmarkRepository(None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="No session available"):
            await repo.record_result(
                model_profile_id="m3",
                task_type="test_write",
                scores={"completion": 0.0},
                success=False,
            )

    @pytest.mark.asyncio
    async def test_adaptive_router_with_benchmark_repo_routes(self):
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            {
                "prompt_profile_id": "p1",
                "model_profile_id": "m1",
                "task_type": "feature",
                "sample_count": 5,
                "composite_score": 0.95,
                "avg_cost": 0.01,
            },
            {
                "prompt_profile_id": "p2",
                "model_profile_id": "m2",
                "task_type": "feature",
                "sample_count": 4,
                "composite_score": 0.80,
                "avg_cost": 0.05,
            },
        ]
        router = AdaptiveRouter(benchmark_repo=repo)
        decision = await router.route(TaskType.FEATURE)
        assert decision.fallback is False
        assert decision.selected_prompt_profile_id == "p1"
        assert decision.selected_model_profile_id == "m1"
        assert decision.sample_count >= 3

    @pytest.mark.asyncio
    async def test_resolve_adaptive_prompt_wired_in_dispatch(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.benchmark import RoutingDecision
        from general_ludd.schemas.todo import Todo, TodoStatus

        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id="best_prompt",
            selected_model_profile_id="best_model",
            composite_score=0.95,
            estimated_cost_usd=0.02,
            sample_count=10,
            fallback=False,
            reason="best_historical_score",
        )
        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)
        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()
        session.delete = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=session,
            http_client=http_client,
            adaptive_router=router,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="test task",
            todo_id="TODO-010",
            status=TodoStatus.ACTIVE,
            work_type="code",
            prompt_profile="default_prompt",
            model_profile="default_model",
        )
        await loop._dispatch_execute_job(todo)
        router.route.assert_called_once()
        call_kwargs = http_client.post.call_args[1]["json"]
        assert call_kwargs["prompt_profile"] == "best_prompt"
        assert call_kwargs["model_profile"] == "best_model"
