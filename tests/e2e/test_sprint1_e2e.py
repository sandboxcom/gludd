"""E2E tests for sprint1 — test through daemon API as a user would."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestE2EAdaptiveRouting:
    @pytest.mark.asyncio
    async def test_e2e_adaptive_routing_end_to_end(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.benchmark import RoutingDecision
        from general_ludd.schemas.todo import Todo, TodoStatus

        router = AsyncMock()
        router.route.return_value = RoutingDecision(
            selected_prompt_profile_id="e2e_prompt",
            selected_model_profile_id="e2e_model",
            composite_score=0.95,
            estimated_cost_usd=0.02,
            sample_count=10,
            fallback=False,
            reason="best_historical_score",
        )

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()
        session.delete = AsyncMock()

        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)

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
            title="e2e adaptive routing test",
            todo_id="TODO-E2E-001",
            status=TodoStatus.ACTIVE,
            work_type="code",
            prompt_profile="static_prompt",
            model_profile="static_model",
        )
        await loop._dispatch_execute_job(todo)

        router.route.assert_called_once()
        call_kwargs = http_client.post.call_args[1]["json"]
        assert call_kwargs["prompt_profile"] == "e2e_prompt"
        assert call_kwargs["model_profile"] == "e2e_model"
        assert call_kwargs["todo_id"] == "TODO-E2E-001"

    @pytest.mark.asyncio
    async def test_e2e_adaptive_routing_records_benchmark(self):
        from general_ludd.schemas.benchmark import TaskType
        from general_ludd.scoring.router import AdaptiveRouter

        repo = AsyncMock()
        repo.get_aggregate_scores.return_value = [
            {
                "prompt_profile_id": "p_best",
                "model_profile_id": "m_best",
                "task_type": "code",
                "sample_count": 5,
                "composite_score": 0.95,
                "avg_cost": 0.01,
            },
        ]
        router = AdaptiveRouter(benchmark_repo=repo)
        decision = await router.route(TaskType.FEATURE)
        assert decision.fallback is False
        assert decision.selected_model_profile_id == "m_best"
        assert decision.selected_prompt_profile_id == "p_best"


class TestE2EWorktreeMonitor:
    def test_e2e_worktree_monitor_end_to_end(self, tmp_path: Path):
        from general_ludd.worktree import WorktreeMonitor, WorktreeMonitorConfig, WorktreeScanner

        wt_dir = tmp_path / "e2e-project"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/e2e-project")
        (wt_dir / "AGENTS.md").write_text("""---
title: E2E Worktree Task
description: This task was auto-discovered from an abandoned worktree
work_type: feature
priority: urgent
queue: core
project: e2e-svc
---
""")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        monitor = WorktreeMonitor(cfg)

        todos = monitor.evaluate()
        assert len(todos) == 1
        assert todos[0]["title"] == "E2E Worktree Task"
        assert todos[0]["status"] == "queued"
        assert todos[0]["tags"] == ["worktree-monitor", "abandoned"]
        assert "todo_id" in todos[0]

        scanner = WorktreeScanner(cfg)
        discovered = scanner.scan()
        assert len(discovered) == 1
        assert discovered[0].agents_md is not None
        assert discovered[0].agents_md.title == "E2E Worktree Task"

    def test_e2e_worktree_monitor_multiple_worktrees(self, tmp_path: Path):
        from general_ludd.worktree import WorktreeMonitor, WorktreeMonitorConfig

        for i in range(3):
            wd = tmp_path / f"e2e-wt-{i}"
            wd.mkdir()
            (wd / ".git").write_text(f"gitdir: /main/.git/worktrees/e2e-wt-{i}")
            (wd / "AGENTS.md").write_text(f"# E2E Task {i}")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
            max_todos_per_scan=10,
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 3


class TestE2ELangGraph:
    @pytest.mark.asyncio
    async def test_e2e_langgraph_quality_retry(self):
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        call_count = 0

        async def mock_fn(profile_id: str, messages: list) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(content="low quality")
            return MagicMock(content="def high_quality():\n    import os\n    return True")

        mock_call = AsyncMock(side_effect=mock_fn)
        gw = LangGraphGateway(
            call_model_fn=mock_call,
            max_retries=2,
            quality_threshold=0.7,
            enable_graph=True,
        )
        gw._has_langgraph = True
        result = await gw.call(messages=[{"role": "user", "content": "write code"}])
        assert call_count > 1
        assert "high_quality" in result["content"]
        assert result["quality_score"] is not None
        assert result["quality_score"] >= 0.7
        assert result["retries"] >= 1

    @pytest.mark.asyncio
    async def test_e2e_langgraph_max_retries_returns_best(self):
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        mock_call = AsyncMock()
        mock_call.return_value = MagicMock(content="low")
        gw = LangGraphGateway(
            call_model_fn=mock_call,
            max_retries=1,
            quality_threshold=0.9,
            enable_graph=True,
        )
        gw._has_langgraph = True
        result = await gw.call(messages=[{"role": "user", "content": "x"}])
        assert result["retries"] <= 1
        assert "low" in result["content"]


class TestE2ERuleModelOverride:
    @pytest.mark.asyncio
    async def test_e2e_rule_model_override(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.todo import Todo, TodoStatus

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()
        session.delete = AsyncMock()

        http_client = AsyncMock()
        http_client.post.return_value = MagicMock(status_code=202)

        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=session,
            http_client=http_client,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="budget exhausted task",
            todo_id="TODO-E2E-010",
            status=TodoStatus.ACTIVE,
            model_profile="expensive_gpt",
            prompt_profile="verbose_template",
            work_type="code",
        )
        loop._tick_state["rule_evaluation_results"] = [
            {
                "todo_id": "TODO-E2E-010",
                "actions": [
                    {
                        "rule_id": "budget_saver",
                        "action_type": "set_model_profile",
                        "params": {"profile_id": "cheap_model"},
                    },
                    {
                        "rule_id": "prompt_shortener",
                        "action_type": "set_prompt_profile",
                        "params": {"profile_id": "concise_template"},
                    },
                ],
            }
        ]
        await loop._dispatch_execute_job(todo)
        call_kwargs = http_client.post.call_args[1]["json"]
        assert call_kwargs["model_profile"] == "cheap_model"
        assert call_kwargs["prompt_profile"] == "concise_template"

    @pytest.mark.asyncio
    async def test_e2e_rule_model_override_with_runner(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.todo import Todo, TodoStatus

        session = AsyncMock()
        db_result = MagicMock()
        db_result.scalars.return_value.all.return_value = []
        session.execute.return_value = db_result
        session.flush = AsyncMock()
        session.delete = AsyncMock()

        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/job"}

        loop = EventLoop(
            session=session,
            runner=runner,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="runner override e2e",
            todo_id="TODO-E2E-011",
            status=TodoStatus.ACTIVE,
            model_profile="orig_model",
            work_type="refactor",
        )
        loop._tick_state["rule_evaluation_results"] = [
            {
                "todo_id": "TODO-E2E-011",
                "actions": [
                    {
                        "rule_id": "r1",
                        "action_type": "set_model_profile",
                        "params": {"profile_id": "cheaper_model"},
                    },
                ],
            }
        ]
        await loop._dispatch_execute_job(todo)
        write_vars_call = runner.write_vars.call_args
        job_vars = write_vars_call[1]["job_vars"]
        assert job_vars["model_profile"] == "cheaper_model"
