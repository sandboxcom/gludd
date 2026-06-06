"""Integration tests for sprint1 objectives."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.worktree import WorktreeMonitor, WorktreeMonitorConfig


class TestAdaptiveRoutingIntegration:
    @pytest.mark.asyncio
    async def test_adaptive_routing_wired_in_daemon(self):
        from general_ludd.daemon import _get_or_create_extended_subsystems

        app = MagicMock()
        app.state._config_dir = None
        sf = AsyncMock()
        ext = _get_or_create_extended_subsystems(app, session_factory=sf)
        assert ext.get("adaptive_router") is not None
        router = ext["adaptive_router"]
        assert router._repo is not None

    @pytest.mark.asyncio
    async def test_adaptive_router_end_to_end_dispatch(self):
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
            title="integration test task",
            todo_id="TODO-INT-001",
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


class TestWorktreeMonitorIntegration:
    def test_worktree_monitor_creates_todo_from_real_agents_md(self, tmp_path: Path):
        wt_dir = tmp_path / "my-project"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/my-project")
        (wt_dir / "AGENTS.md").write_text("""---
title: Integrate login service
description: Connect the login page to the backend auth service
work_type: feature
priority: high
queue: core
project: auth-svc
---
""")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 1
        assert todos[0]["title"] == "Integrate login service"
        assert todos[0]["work_type"] == "feature"
        assert todos[0]["priority"] == "high"
        assert todos[0]["queue"] == "core"
        assert todos[0]["project_id"] == "auth-svc"
        assert todos[0]["status"] == "queued"

    def test_worktree_monitor_without_agents_md_no_scan(self, tmp_path: Path):
        wt_dir = tmp_path / "no-md"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/no-md")

        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_worktree_monitor_scan_and_track(self, tmp_path: Path):
        for i in range(3):
            wt_dir = tmp_path / f"project-{i}"
            wt_dir.mkdir()
            (wt_dir / ".git").write_text(f"gitdir: /main/.git/worktrees/project-{i}")
            (wt_dir / "AGENTS.md").write_text(f"# Task {i}")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
            max_todos_per_scan=5,
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 3
        assert len(monitor.tracked_worktrees) == 3

    def test_worktree_monitor_deduplicates_across_scans(self, tmp_path: Path):
        wt_dir = tmp_path / "dedup-test"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/dedup-test")
        (wt_dir / "AGENTS.md").write_text("# Dedup task")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        monitor = WorktreeMonitor(cfg)
        todos1 = monitor.evaluate()
        assert len(todos1) == 1
        todos2 = monitor.evaluate()
        assert len(todos2) == 0

    def test_worktree_monitor_respects_exclude_patterns(self, tmp_path: Path):
        included = tmp_path / "include-me"
        included.mkdir()
        (included / ".git").write_text("gitdir: /main/.git/worktrees/include-me")
        (included / "AGENTS.md").write_text("# Include me")

        excluded = tmp_path / "node_modules"
        excluded.mkdir()
        (excluded / ".git").write_text("gitdir: /main/.git/worktrees/nodemod")
        (excluded / "AGENTS.md").write_text("# Exclude me")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
            exclude_patterns=["*node_modules*"],
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 1
        assert todos[0]["title"] == "Include me"


class TestLangGraphIntegration:
    @pytest.mark.asyncio
    async def test_langgraph_multi_step_cycle(self):
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        call_count = 0

        async def mock_fn(profile_id: str, messages: list) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(content="bad output")
            return MagicMock(content="def good_code():\n    import os\n    return True")

        mock_call = AsyncMock(side_effect=mock_fn)
        router = AsyncMock()
        router.route.return_value = MagicMock(
            selected_prompt_profile_id="best",
            selected_model_profile_id="best",
            composite_score=0.8,
            estimated_cost_usd=0.01,
            sample_count=5,
            fallback=False,
            reason="best",
        )
        gw = LangGraphGateway(
            call_model_fn=mock_call,
            adaptive_router=router,
            max_retries=2,
            quality_threshold=0.7,
            enable_graph=True,
        )
        gw._has_langgraph = True
        result = await gw.call(
            messages=[{"role": "user", "content": "write code"}],
            task_context={"work_type": "code"},
            profile_id="default",
        )
        assert call_count > 1
        assert "good_code" in result["content"]
        assert result["quality_score"] is not None
        assert result["quality_score"] >= 0.7

    @pytest.mark.asyncio
    async def test_langgraph_fallback_single_shot(self):
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        mock_call = AsyncMock()
        mock_call.return_value = MagicMock(content="single shot result")
        gw = LangGraphGateway(call_model_fn=mock_call, enable_graph=False)
        result = await gw.call(
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result["content"] == "single shot result"
        assert result["retries"] == 0
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_langgraph_max_retries_enforced(self):
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        mock_call = AsyncMock()
        mock_call.return_value = MagicMock(content="always low quality")
        gw = LangGraphGateway(
            call_model_fn=mock_call,
            max_retries=1,
            quality_threshold=0.9,
            enable_graph=True,
        )
        gw._has_langgraph = True
        result = await gw.call(
            messages=[{"role": "user", "content": "x"}],
        )
        assert result["retries"] <= 1
        assert len(result["warnings"]) > 0


class TestRuleModelActionIntegration:
    @pytest.mark.asyncio
    async def test_rule_changes_model_mid_dispatch(self):
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
            title="rule override test",
            todo_id="TODO-RULE-001",
            status=TodoStatus.ACTIVE,
            model_profile="expensive_model",
            prompt_profile="verbose_prompt",
            work_type="code",
        )
        loop._tick_state["rule_evaluation_results"] = [
            {
                "todo_id": "TODO-RULE-001",
                "actions": [
                    {
                        "rule_id": "budget_rule",
                        "action_type": "set_model_profile",
                        "params": {"profile_id": "cheap_model"},
                    },
                    {
                        "rule_id": "concise_rule",
                        "action_type": "set_prompt_profile",
                        "params": {"profile_id": "concise_prompt"},
                    },
                ],
            }
        ]
        await loop._dispatch_execute_job(todo)
        call_kwargs = http_client.post.call_args[1]["json"]
        assert call_kwargs["model_profile"] == "cheap_model"
        assert call_kwargs["prompt_profile"] == "concise_prompt"

    @pytest.mark.asyncio
    async def test_rule_adaptive_routing_toggle(self):
        from general_ludd.rules.engine import apply_rule_actions

        actions = [{"type": "enable_adaptive_routing", "value": False}]
        overrides = apply_rule_actions(actions)
        assert overrides["enable_adaptive_routing"] is False

        actions_on = [{"type": "enable_adaptive_routing", "value": True}]
        overrides_on = apply_rule_actions(actions_on)
        assert overrides_on["enable_adaptive_routing"] is True


class TestFeedbackLoopIntegration:
    def test_feedback_loop_complete_task_records_benchmark(self):
        task_return = {
            "return_id": "RET-100",
            "todo_id": "TODO-100",
            "model_profile": "gpt4",
            "work_type": "code",
            "success": True,
            "input_tokens": 2000,
            "output_tokens": 500,
            "cost_usd": 0.05,
            "time_seconds": 3.5,
        }
        scores = _compute_benchmark_scores(task_return)
        assert scores["completion"] == 1.0
        assert scores["code_quality"] == 0.5
        assert scores["token_efficiency"] > 0
        assert scores["instruction"] == 1.0

    def test_feedback_loop_failed_task_low_scores(self):
        task_return = {
            "return_id": "RET-101",
            "success": False,
            "error_message": "runtime error",
        }
        scores = _compute_benchmark_scores(task_return)
        assert scores["completion"] == 0.0
        assert scores["instruction"] == 0.5

    def test_feedback_loop_high_test_pass_rate(self):
        import json

        task_return = {
            "return_id": "RET-102",
            "success": True,
            "artifacts": json.dumps({"tests_passed": 48, "tests_total": 50}),
        }
        scores = _compute_benchmark_scores(task_return)
        assert scores["code_quality"] == pytest.approx(48 / 50)

    def test_feedback_loop_no_test_data_defaults(self):
        task_return = {"return_id": "RET-103", "success": True}
        scores = _compute_benchmark_scores(task_return)
        assert scores["code_quality"] == 0.5


def _compute_benchmark_scores(task_return: dict) -> dict[str, float]:
    import json

    success = task_return.get("success", False)
    completion_score = 1.0 if success else 0.0

    code_quality_score = 0.5
    artifacts_raw = task_return.get("artifacts", "{}")
    try:
        artifacts = json.loads(artifacts_raw) if isinstance(artifacts_raw, str) else artifacts_raw
    except (json.JSONDecodeError, TypeError):
        artifacts = {}
    if isinstance(artifacts, dict):
        total = artifacts.get("tests_total", 0)
        passed = artifacts.get("tests_passed", 0)
        if total > 0:
            code_quality_score = passed / total

    instruction_score = 0.5
    if success:
        instruction_score = 1.0

    input_tokens = task_return.get("input_tokens", 1000)
    token_score = min(1.0, 1000.0 / max(float(input_tokens), 1.0))

    return {
        "completion": completion_score,
        "code_quality": code_quality_score,
        "instruction": instruction_score,
        "token_efficiency": token_score,
    }
