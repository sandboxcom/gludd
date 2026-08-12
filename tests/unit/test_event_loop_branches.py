"""Targeted branch coverage tests for event_loop/loop.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import (
    _CODE_WORK_TYPES,
    _TOOL_USE_WORK_TYPES,
    _WORK_TYPE_TASK_TYPE_MAP,
    DISPATCH_PHASE_INDEX,
    PHASE_ORDER,
    _FileClaimConflict,
    _format_acceptance_criteria,
    _resolve_prompt_text_static,
    _safe_str,
    _self_update_work_item_from_todo,
)
from general_ludd.schemas.task_return import TaskReturnStatus
from general_ludd.schemas.todo import TodoStatus


class TestFormatAcceptanceCriteria:
    def test_empty_string(self):
        result = _format_acceptance_criteria("")
        assert result == ""

    def test_none_input(self):
        result = _format_acceptance_criteria(None)
        assert result == ""

    def test_valid_json_list(self):
        raw = json.dumps(["Must pass tests", "Must be green"])
        result = _format_acceptance_criteria(raw)
        assert "Must pass tests" in result
        assert "Must be green" in result
        assert result.startswith("- ")

    def test_invalid_json_returns_raw(self):
        raw = "not valid json {"
        result = _format_acceptance_criteria(raw)
        assert result == raw

    def test_non_list_json_returns_raw(self):
        raw = '{"key": "val"}'
        result = _format_acceptance_criteria(raw)
        assert result == raw

    def test_empty_json_list(self):
        result = _format_acceptance_criteria("[]")
        assert result == ""


class TestSafeStr:
    def test_attr_exists_str(self):
        obj = type("X", (), {"name": "hello"})()
        result = _safe_str(obj, "name")
        assert result == "hello"

    def test_attr_missing_default(self):
        obj = type("X", (), {})()
        result = _safe_str(obj, "missing", "fallback")
        assert result == "fallback"

    def test_attr_missing_no_default(self):
        obj = type("X", (), {})()
        result = _safe_str(obj, "missing")
        assert result is None

    def test_attr_exists_non_str(self):
        obj = type("X", (), {"count": 42})()
        result = _safe_str(obj, "count", "default")
        assert result == "default"


class TestSelfUpdateWorkItemFromTodo:
    def test_no_tags(self):
        todo = type("X", (), {"tags": None})()
        result = _self_update_work_item_from_todo(todo, "TODO-001")
        assert result is not None

    def test_empty_tags(self):
        todo = type("X", (), {"tags": []})()
        result = _self_update_work_item_from_todo(todo, "TODO-001")
        assert result is not None

    def test_tier_tag_code(self):
        todo = type("X", (), {"tags": ["tier:code"]})()
        result = _self_update_work_item_from_todo(todo, "TODO-001")
        assert result is not None

    def test_tier_tag_unknown(self):
        todo = type("X", (), {"tags": ["tier:nonexistent"]})()
        result = _self_update_work_item_from_todo(todo, "TODO-001")
        assert result is not None


class TestResolvePromptTextStatic:
    def test_no_profile(self):
        result = _resolve_prompt_text_static(None, None)
        assert result is None

    def test_profile_project_templates_dir(self):
        path_mock = MagicMock()
        path_mock.is_file.return_value = True
        with patch("pathlib.Path", return_value=path_mock), patch("jinja2.sandbox.SandboxedEnvironment") as env_cls:
            env_cls.return_value.get_template.return_value.render.return_value = "rendered"
            result = _resolve_prompt_text_static(None, "template.j2", project_templates_dir="/tmp/templates")
            assert result == "rendered"

    def test_profile_project_templates_dir_not_file(self):
        path_mock = MagicMock()
        path_mock.is_file.return_value = False
        with patch("pathlib.Path", return_value=path_mock):
            registry = MagicMock()
            registry.render.return_value = "from registry"
            result = _resolve_prompt_text_static(registry, "template.j2", project_templates_dir="/tmp/templates")
            assert result == "from registry"

    def test_registry_render_failure(self):
        registry = MagicMock()
        registry.render.side_effect = Exception("render error")
        result = _resolve_prompt_text_static(registry, "profile")
        assert result is None

    def test_registry_none_returns_none(self):
        result = _resolve_prompt_text_static(None, "profile")
        assert result is None


class TestWorkTypeMaps:
    def test_tool_use_work_types(self):
        assert "code" in _TOOL_USE_WORK_TYPES
        assert "analysis" in _TOOL_USE_WORK_TYPES
        assert "bug_fix" in _TOOL_USE_WORK_TYPES

    def test_code_work_types(self):
        assert "code" in _CODE_WORK_TYPES
        assert "bug_fix" in _CODE_WORK_TYPES
        assert "test" in _CODE_WORK_TYPES

    def test_work_type_task_type_map(self):
        assert _WORK_TYPE_TASK_TYPE_MAP["code"] == "feature"
        assert _WORK_TYPE_TASK_TYPE_MAP["bug_fix"] == "bug_fix"
        assert _WORK_TYPE_TASK_TYPE_MAP["test"] == "test_write"
        assert _WORK_TYPE_TASK_TYPE_MAP["review"] == "code_review"
        assert _WORK_TYPE_TASK_TYPE_MAP["docs"] == "documentation"
        assert _WORK_TYPE_TASK_TYPE_MAP["security"] == "security_fix"

    def test_refactor_in_map(self):
        assert _WORK_TYPE_TASK_TYPE_MAP["refactor"] == "refactor"

    def test_audit_in_map(self):
        assert _WORK_TYPE_TASK_TYPE_MAP["audit"] == "feature"


class TestPhaseOrder:
    def test_dispatch_phase_index(self):
        assert PHASE_ORDER[DISPATCH_PHASE_INDEX] == "dispatch_execute_jobs"

    def test_phase_order_contains_key_phases(self):
        for phase in (
            "claim_runnable_todos",
            "dispatch_execute_jobs",
            "purge_old_task_decisions",
            "self_improve",
            "emit_tick_metrics",
            "load_config_snapshot",
            "evaluate_rules",
            "reconcile_completed_decisions",
        ):
            assert phase in PHASE_ORDER, f"{phase} not in PHASE_ORDER"

    def test_phase_order_length(self):
        assert len(PHASE_ORDER) >= 18

    def test_refill_task_buckets_in_phase_order(self):
        assert "refill_task_buckets" in PHASE_ORDER

    def test_run_scheduler_in_phase_order(self):
        assert "run_scheduler" in PHASE_ORDER


class TestFileClaimConflict:
    def test_is_exception(self):
        e = _FileClaimConflict()
        assert isinstance(e, Exception)

    def test_can_be_caught_as_exception(self):
        caught = False
        try:
            raise _FileClaimConflict()
        except _FileClaimConflict:
            caught = True
        assert caught

    def test_not_caught_as_valueerror(self):
        caught = False
        try:
            raise _FileClaimConflict()
        except ValueError:
            caught = True
        except Exception:
            pass
        assert not caught


class TestPhasePurgeOldTaskDecisions:
    @pytest.mark.asyncio
    async def test_no_active_session_returns_early(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = None
        loop._tick_metrics = {}
        loop.config = {}
        loop._total_ticks = 0
        await loop._phase_purge_old_task_decisions()
        assert "task_decisions_purged" not in loop._tick_metrics

    @pytest.mark.asyncio
    async def test_interval_not_matched_skips(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = MagicMock()
        loop._tick_metrics = {}
        loop.config = {"task_decisions_retention_interval_ticks": "3600"}
        loop._total_ticks = 1
        await loop._phase_purge_old_task_decisions()
        assert "task_decisions_purged" not in loop._tick_metrics

    @pytest.mark.asyncio
    async def test_interval_matched_triggers_cleanup(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = AsyncMock()
        loop._tick_metrics = {}
        loop.config = {
            "task_decisions_retention_interval_ticks": "100",
            "task_decisions_retention_days": "30",
        }
        loop._total_ticks = 100
        with patch(
            "general_ludd.event_loop.loop_handlers.cleanup_old_task_decisions",
            new=AsyncMock(return_value=5),
        ):
            await loop._phase_purge_old_task_decisions()
        assert loop._tick_metrics["task_decisions_purged"] == 5

    @pytest.mark.asyncio
    async def test_interval_zero_skips(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = MagicMock()
        loop._tick_metrics = {}
        loop.config = {"task_decisions_retention_interval_ticks": "0"}
        loop._total_ticks = 0
        await loop._phase_purge_old_task_decisions()
        assert "task_decisions_purged" not in loop._tick_metrics

    @pytest.mark.asyncio
    async def test_exception_handled_gracefully(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = AsyncMock()
        loop._tick_metrics = {}
        loop.config = {"task_decisions_retention_interval_ticks": "1"}
        loop._total_ticks = 1
        with patch(
            "general_ludd.event_loop.loop_handlers.cleanup_old_task_decisions",
            new=AsyncMock(side_effect=RuntimeError("db error")),
        ):
            await loop._phase_purge_old_task_decisions()
        assert "task_decisions_purged" not in loop._tick_metrics


class TestPhaseRefillTaskBuckets:
    @pytest.mark.asyncio
    async def test_no_active_session_still_works(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = None
        loop._todo_repo = MagicMock()
        loop._tick_metrics = {}
        await loop._phase_refill_task_buckets()

    @pytest.mark.asyncio
    async def test_with_session_reclaims_leases(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = AsyncMock()
        loop._todo_repo = MagicMock()
        loop._tick_metrics = {}
        loop._reap_stuck_todos = AsyncMock()
        with patch(
            "general_ludd.event_loop.loop.reclaim_expired_leases",
            new=AsyncMock(return_value=3),
        ):
            await loop._phase_refill_task_buckets()
        assert loop._tick_metrics["leases_reclaimed"] == 3

    @pytest.mark.asyncio
    async def test_without_repo_still_reclaims(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._active_session = AsyncMock()
        loop._todo_repo = None
        loop._tick_metrics = {}
        with patch(
            "general_ludd.event_loop.loop.reclaim_expired_leases",
            new=AsyncMock(return_value=0),
        ):
            await loop._phase_refill_task_buckets()
        assert loop._tick_metrics["leases_reclaimed"] == 0


class TestPhaseRunScheduler:
    @pytest.mark.asyncio
    async def test_no_repo_returns(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = None
        loop._active_session = MagicMock()
        loop._tick_metrics = {}
        await loop._phase_run_scheduler()
        assert "scheduled_promoted" not in loop._tick_metrics

    @pytest.mark.asyncio
    async def test_no_session_returns(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = MagicMock()
        loop._active_session = None
        loop._tick_metrics = {}
        await loop._phase_run_scheduler()
        assert "scheduled_promoted" not in loop._tick_metrics

    @pytest.mark.asyncio
    async def test_with_repo_and_session(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = MagicMock()
        loop._active_session = MagicMock()
        loop._tick_metrics = {}
        with patch("general_ludd.event_loop.scheduler.TodoScheduler") as sched_cls:
            sched_cls.return_value.tick = AsyncMock(return_value=(2, 1))
            await loop._phase_run_scheduler()
        assert loop._tick_metrics["scheduled_promoted"] == 2
        assert loop._tick_metrics["scheduled_spawned"] == 1


class TestPhaseClaimRunnableTodos:
    @pytest.mark.asyncio
    async def test_no_todo_repo(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = None
        loop._tick_metrics = {}
        loop._tick_state = {}
        await loop._phase_claim_runnable_todos()

    @pytest.mark.asyncio
    async def test_paused_project_skips(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = MagicMock()
        loop._tick_metrics = {}
        loop._tick_state = {}
        loop._pause_controller = MagicMock()
        loop._pause_controller.is_paused.return_value = True
        loop._tick_project_id = "proj-1"
        await loop._phase_claim_runnable_todos()
        assert loop._tick_state["claimed_todos"] == []

    @pytest.mark.asyncio
    async def test_no_project_id_skips(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._todo_repo = MagicMock()
        loop._tick_metrics = {}
        loop._tick_state = {}
        loop._pause_controller = None
        loop._tick_project_id = None
        loop._project_manager = MagicMock()
        await loop._phase_claim_runnable_todos()
        assert loop._tick_state["claimed_todos"] == []


class TestPhaseSdlcGate:
    @pytest.mark.asyncio
    async def test_non_dict_config_returns_early(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        object.__setattr__(loop, "config", None)
        loop._tick_state = {}
        await loop._phase_sdlc_gate()
        assert "sdlc_gate_results" not in loop._tick_state

    @pytest.mark.asyncio
    async def test_no_sdlc_config_key(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop.config = {}
        loop._tick_state = {}
        await loop._phase_sdlc_gate()
        assert "sdlc_gate_results" not in loop._tick_state

    @pytest.mark.asyncio
    async def test_sdlc_enforce_true_with_blocked_stages(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop.config = {
            "ai_sdlc": {
                "enforce": True,
                "pipeline_stages": {
                    "stage1": {
                        "entry_gates": {"lint": {"required": True}},
                        "exit_gates": {},
                    }
                },
            }
        }
        loop._tick_state = {}
        await loop._phase_sdlc_gate()
        assert loop._tick_state["sdlc_gate_results"]["stages_blocked"] == 1
        assert loop._tick_state["sdlc_gate_results"]["stages_checked"] == 1

    @pytest.mark.asyncio
    async def test_sdlc_enforce_false_still_records(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop.config = {
            "ai_sdlc": {
                "enforce": False,
                "pipeline_stages": {
                    "stage1": {
                        "entry_gates": {"lint": {"required": True}},
                        "exit_gates": {},
                    }
                },
            }
        }
        loop._tick_state = {}
        await loop._phase_sdlc_gate()
        result = loop._tick_state["sdlc_gate_results"]
        assert result["stages_blocked"] == 1
        assert result["stages_checked"] == 1

    @pytest.mark.asyncio
    async def test_artifact_dir_missing_blocks_entry(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop.config = {
            "ai_sdlc": {
                "enforce": False,
                "pipeline_stages": {
                    "stage1": {
                        "artifact_dir": "/nonexistent/path/xyz",
                        "entry_gates": {},
                        "exit_gates": {},
                    }
                },
            }
        }
        loop._tick_state = {}
        await loop._phase_sdlc_gate()
        result = loop._tick_state["sdlc_gate_results"]
        assert result["stages_blocked"] == 1
        assert "entry_passed" in result["stage_results"]["stage1"]
        assert result["stage_results"]["stage1"]["entry_passed"] is False

    @pytest.mark.asyncio
    async def test_non_dict_stage_spec_skipped(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop.config = {
            "ai_sdlc": {
                "enforce": False,
                "pipeline_stages": {
                    "bad_stage": "not a dict",
                },
            }
        }
        loop._tick_state = {}
        await loop._phase_sdlc_gate()
        result = loop._tick_state["sdlc_gate_results"]
        assert result["stages_checked"] == 0
        assert result["stages_blocked"] == 0


class TestDispatchReturnReview:
    @pytest.mark.asyncio
    async def test_not_created_status_skipped(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.task_return import TaskReturn

        loop = EventLoop.__new__(EventLoop)
        loop.config = {}
        tr = TaskReturn(
            return_id="RET-1",
            todo_id="TODO-1",
            job_id="JOB-1",
            playbook="test.yml",
            queue="core",
            status=TaskReturnStatus.REVIEWED,
        )
        result = await loop.dispatch_return_review(tr)
        assert result["status"] == "skipped"
        assert result["reason"] == "not_created"

    @pytest.mark.asyncio
    async def test_created_status_dispatches(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.task_return import TaskReturn

        loop = EventLoop.__new__(EventLoop)
        loop.config = {}
        tr = TaskReturn(
            return_id="RET-1",
            todo_id="TODO-1",
            job_id="JOB-1",
            playbook="test.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        result = await loop.dispatch_return_review(tr)
        assert result["status"] == "dispatched"
        assert "job_id" in result
        assert result["job_id"].startswith("REVIEW-")

    @pytest.mark.asyncio
    async def test_archived_status_skipped(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.task_return import TaskReturn

        loop = EventLoop.__new__(EventLoop)
        loop.config = {}
        tr = TaskReturn(
            return_id="RET-2",
            todo_id="TODO-2",
            job_id="JOB-2",
            playbook="test.yml",
            queue="core",
            status=TaskReturnStatus.ARCHIVED,
        )
        result = await loop.dispatch_return_review(tr)
        assert result["status"] == "skipped"


class TestClaimRunnableTodos:
    @pytest.mark.asyncio
    async def test_filters_queued_only(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.todo import Todo

        loop = EventLoop.__new__(EventLoop)
        todos = [
            Todo(todo_id="T1", status=TodoStatus.QUEUED, title="a", project_id="p1"),
            Todo(todo_id="T2", status=TodoStatus.ACTIVE, title="b", project_id="p1"),
            Todo(todo_id="T3", status=TodoStatus.QUEUED, title="c", project_id="p1"),
        ]
        result = await loop.claim_runnable_todos(todos)
        assert len(result) == 2
        assert result[0].todo_id == "T1"
        assert result[1].todo_id == "T3"

    @pytest.mark.asyncio
    async def test_all_queued_returns_all(self):
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.todo import Todo

        loop = EventLoop.__new__(EventLoop)
        todos = [
            Todo(todo_id="T1", status=TodoStatus.QUEUED, title="a", project_id="p1"),
            Todo(todo_id="T2", status=TodoStatus.QUEUED, title="b", project_id="p1"),
        ]
        result = await loop.claim_runnable_todos(todos)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_list(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        result = await loop.claim_runnable_todos([])
        assert result == []


class TestPhaseEmitTickMetrics:
    @pytest.mark.asyncio
    async def test_logs_metrics(self):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop.__new__(EventLoop)
        loop._tick_metrics = {"test": 42}
        await loop._phase_emit_tick_metrics()
