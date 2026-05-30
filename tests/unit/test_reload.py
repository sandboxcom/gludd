"""Unit tests for reload manager and self-improvement workflow."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from agentic_harness.reload.manager import (
    ReloadManager,
    ReloadResult,
    ReloadStatus,
    ReloadType,
)
from agentic_harness.reload.self_improve import (
    ApplyResult,
    SelfImprovementWorkflow,
)

PLAYBOOK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "playbooks")


class TestReloadTypeEnum:
    def test_reload_type_enum(self) -> None:
        assert ReloadType.CONFIG.value == "config"
        assert ReloadType.PROMPTS.value == "prompts"
        assert ReloadType.RULES.value == "rules"
        assert ReloadType.WORKER_CODE.value == "worker_code"
        assert ReloadType.EVENT_LOOP_CODE.value == "event_loop_code"
        assert ReloadType.SCHEMA_MIGRATION.value == "schema_migration"


class TestReloadManagerRequestReload:
    def test_reload_manager_request_reload(self) -> None:
        mgr = ReloadManager()
        result = mgr.request_reload(ReloadType.CONFIG, {"key": "val"})
        assert isinstance(result, ReloadResult)
        assert result.reload_type == ReloadType.CONFIG
        assert result.status == "pending"
        assert result.reload_id


class TestReloadManagerExecuteConfigReload:
    def test_reload_manager_execute_config_reload(self) -> None:
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.CONFIG)
        result = mgr.execute_reload(rr.reload_id)
        assert result.status == "success"
        assert result.reload_type == ReloadType.CONFIG


class TestReloadManagerExecutePromptReload:
    def test_reload_manager_execute_prompt_reload(self) -> None:
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.PROMPTS)
        result = mgr.execute_reload(rr.reload_id)
        assert result.status == "success"
        assert result.reload_type == ReloadType.PROMPTS


class TestReloadManagerExecuteWorkerCodeReload:
    def test_reload_manager_execute_worker_code_reload(self) -> None:
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.WORKER_CODE)
        result = mgr.execute_reload(rr.reload_id)
        assert result.status == "success"
        assert result.reload_type == ReloadType.WORKER_CODE


class TestReloadManagerRollbackFailedReload:
    def test_reload_manager_rollback_failed_reload(self) -> None:
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.CONFIG)
        mgr.execute_reload(rr.reload_id)
        mgr._reload_store[rr.reload_id]["status"] = "failed"
        result = mgr.rollback(rr.reload_id)
        assert result.status == "rolled_back"


class TestReloadManagerGetStatus:
    def test_reload_manager_get_status(self) -> None:
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.RULES)
        mgr.execute_reload(rr.reload_id)
        status = mgr.get_reload_status(rr.reload_id)
        assert isinstance(status, ReloadStatus)
        assert status.status == "success"
        assert status.reload_id == rr.reload_id
        assert status.started_at is not None
        assert status.completed_at is not None


class TestSelfImprovementCreateTodo:
    def test_self_improvement_create_todo(self) -> None:
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo(
            title="Optimize event loop", description="Reduce latency"
        )
        assert todo["title"] == "Optimize event loop"
        assert todo["description"] == "Reduce latency"
        assert todo["status"] == "pending"
        assert todo["todo_id"]


class TestSelfImprovementValidatePasses:
    @patch("agentic_harness.validation.runner.subprocess.run")
    def test_self_improvement_validate_passes(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="3 passed", stderr="")
        wf = SelfImprovementWorkflow()
        result = wf.validate_improvement("/tmp/worktree")
        assert result.success is True
        assert result.passed_count == 3


class TestSelfImprovementValidateFails:
    @patch("agentic_harness.validation.runner.subprocess.run")
    def test_self_improvement_validate_fails(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="1 passed, 1 failed\nFAILED test_x.py::test_a",
            stderr="",
        )
        wf = SelfImprovementWorkflow()
        result = wf.validate_improvement("/tmp/worktree")
        assert result.success is False
        assert result.failed_count == 1


class TestSelfImprovementApplyAndReload:
    def test_self_improvement_apply_and_reload(self) -> None:
        wf = SelfImprovementWorkflow()
        from agentic_harness.validation.runner import ValidationResult

        vresult = ValidationResult(
            success=True, passed_count=5, failed_count=0, output="5 passed"
        )
        apply_result = wf.apply_improvement("TODO-1", vresult)
        assert isinstance(apply_result, ApplyResult)
        assert apply_result.applied is True
        assert apply_result.validation_passed is True
        assert apply_result.reload_needed is True

        reload_result = wf.reload_if_needed(apply_result)
        assert reload_result.status == "success"


class TestSelfImprovementNoReloadWhenNotNeeded:
    def test_self_improvement_no_reload_when_not_needed(self) -> None:
        wf = SelfImprovementWorkflow()
        from agentic_harness.validation.runner import ValidationResult

        vresult = ValidationResult(
            success=False, passed_count=0, failed_count=1, output="1 failed"
        )
        apply_result = wf.apply_improvement("TODO-2", vresult)
        assert apply_result.applied is False
        assert apply_result.reload_needed is False

        reload_result = wf.reload_if_needed(apply_result)
        assert reload_result.status == "pending"
        assert "not needed" in reload_result.message


class TestPlaybooksExist:
    def test_playbooks_exist(self) -> None:
        assert os.path.isfile(os.path.join(PLAYBOOK_DIR, "self_improve_harness.yml"))
        assert os.path.isfile(os.path.join(PLAYBOOK_DIR, "reload_harness.yml"))
