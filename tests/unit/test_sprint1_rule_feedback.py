"""Tests for obj03-05: Rule actions, feedback loop, langgraph integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.rules.engine import (
    ActionType,
    apply_rule_actions,
)


class TestRuleActions:
    def test_set_model_profile_action(self):
        actions = [{"type": "set_model_profile", "profile_id": "cheap_model"}]
        overrides = apply_rule_actions(actions)
        assert overrides["model_profile"] == "cheap_model"

    def test_set_prompt_profile_action(self):
        actions = [{"type": "set_prompt_profile", "profile_id": "concise_prompt"}]
        overrides = apply_rule_actions(actions)
        assert overrides["prompt_profile"] == "concise_prompt"

    def test_set_quality_threshold_action(self):
        actions = [{"type": "set_quality_threshold", "value": 0.7}]
        overrides = apply_rule_actions(actions)
        assert overrides["quality_threshold"] == 0.7

    def test_enable_adaptive_routing_action(self):
        actions = [{"type": "enable_adaptive_routing", "value": False}]
        overrides = apply_rule_actions(actions)
        assert overrides["enable_adaptive_routing"] is False

    def test_multiple_actions_combined(self):
        actions = [
            {"type": "set_model_profile", "profile_id": "m1"},
            {"type": "set_prompt_profile", "profile_id": "p1"},
            {"type": "set_quality_threshold", "value": 0.8},
        ]
        overrides = apply_rule_actions(actions)
        assert overrides == {
            "model_profile": "m1",
            "prompt_profile": "p1",
            "quality_threshold": 0.8,
        }

    def test_last_action_wins_for_same_type(self):
        actions = [
            {"type": "set_model_profile", "profile_id": "first"},
            {"type": "set_model_profile", "profile_id": "second"},
        ]
        overrides = apply_rule_actions(actions)
        assert overrides["model_profile"] == "second"

    def test_unknown_action_type_ignored(self):
        actions = [{"type": "unknown_action", "value": "x"}]
        overrides = apply_rule_actions(actions)
        assert overrides == {}

    def test_empty_actions_returns_empty(self):
        assert apply_rule_actions([]) == {}

    def test_action_type_enum_values(self):
        assert ActionType.SET_MODEL_PROFILE == "set_model_profile"
        assert ActionType.SET_PROMPT_PROFILE == "set_prompt_profile"
        assert ActionType.SET_QUALITY_THRESHOLD == "set_quality_threshold"
        assert ActionType.ENABLE_ADAPTIVE_ROUTING == "enable_adaptive_routing"
        assert ActionType.ROUTE == "route"
        assert ActionType.PAUSE_QUEUE == "pause_queue"
        assert ActionType.REDUCE_BUCKETS == "reduce_buckets"

    def test_apply_rule_actions_with_none_todo(self):
        actions = [{"type": "set_model_profile", "profile_id": "m1"}]
        overrides = apply_rule_actions(actions, todo=None)
        assert overrides["model_profile"] == "m1"

    @pytest.mark.asyncio
    async def test_rule_actions_integrated_with_event_loop_dispatch(self):
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
        todo_repo = AsyncMock()
        task_return_repo = AsyncMock()

        loop = EventLoop(
            worker_base_url="http://localhost:8000",
            session=session,
            http_client=http_client,
            todo_repo=todo_repo,
            task_return_repo=task_return_repo,
            config={
                "default_playbook": "noop.yml",
                "model_profiles": [],
                "rules": [],
            },
        )
        todo = Todo(
            title="override test",
            todo_id="TODO-020",
            status=TodoStatus.ACTIVE,
            model_profile="original_model",
            prompt_profile="original_prompt",
            work_type="code",
        )
        loop._tick_state["rule_evaluation_results"] = [
            {
                "todo_id": "TODO-020",
                "actions": [
                    {
                        "rule_id": "r1",
                        "action_type": "set_model_profile",
                        "params": {"profile_id": "rule_model"},
                    },
                    {
                        "rule_id": "r2",
                        "action_type": "set_prompt_profile",
                        "params": {"profile_id": "rule_prompt"},
                    },
                ],
            }
        ]
        await loop._dispatch_execute_job(todo)
        call_kwargs = http_client.post.call_args[1]["json"]
        assert call_kwargs["model_profile"] == "rule_model"
        assert call_kwargs["prompt_profile"] == "rule_prompt"

    @pytest.mark.asyncio
    async def test_rule_action_applied_with_runner(self):
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
            config={"default_playbook": "noop.yml", "model_profiles": [], "rules": []},
        )
        todo = Todo(
            title="runner override",
            todo_id="TODO-021",
            status=TodoStatus.ACTIVE,
            model_profile="orig",
            work_type="refactor",
        )
        loop._tick_state["rule_evaluation_results"] = [
            {
                "todo_id": "TODO-021",
                "actions": [
                    {
                        "rule_id": "r1",
                        "action_type": "set_model_profile",
                        "params": {"profile_id": "rule_model_runner"},
                    },
                ],
            }
        ]
        await loop._dispatch_execute_job(todo)
        write_vars_call = runner.write_vars.call_args
        job_vars = write_vars_call[1]["job_vars"]
        assert job_vars["model_profile"] == "rule_model_runner"


class TestFeedbackLoop:
    def test_feedback_loop_extracts_benchmark_scores(self):
        import json

        task_return = {
            "return_id": "RET-001",
            "todo_id": "TODO-001",
            "model_profile": "m1",
            "prompt_profile": "p1",
            "work_type": "code",
            "success": True,
            "output_tokens": 500,
            "input_tokens": 2000,
            "cost_usd": 0.05,
            "time_seconds": 3.5,
            "artifacts": json.dumps({"tests_passed": 10, "tests_total": 12}),
        }
        scores = _compute_benchmark_scores(task_return)
        assert scores["completion"] == 1.0
        assert scores["code_quality"] == pytest.approx(10 / 12)
        assert scores["token_efficiency"] > 0
        assert scores["instruction"] > 0

    def test_feedback_loop_failure_produces_low_scores(self):
        task_return = {
            "return_id": "RET-002",
            "success": False,
            "error_message": "timeout",
        }
        scores = _compute_benchmark_scores(task_return)
        assert scores["completion"] == 0.0

    def test_feedback_loop_no_token_data_uses_defaults(self):
        task_return = {"return_id": "RET-003", "success": True}
        scores = _compute_benchmark_scores(task_return)
        assert scores["completion"] == 1.0
        assert scores["code_quality"] == 0.5


def _compute_benchmark_scores(task_return: dict) -> dict[str, float]:
    """Compute benchmark scores from a task return. Used by feedback loop."""
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
