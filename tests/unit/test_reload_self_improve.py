"""Structural and behavioral tests for reload.self_improve module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.reload.manager import ReloadManager, ReloadResult, ReloadType
from general_ludd.reload.self_improve import (
    ApplyResult,
    SelfImprovementWorkflow,
    _HARDCODED_FALLBACK,
    _resolve_test_commands,
)


class TestModuleImports:
    def test_all_exports_importable(self) -> None:
        assert ApplyResult is not None
        assert SelfImprovementWorkflow is not None
        assert _HARDCODED_FALLBACK is not None
        assert _resolve_test_commands is not None

    def test_module_has_no_unexpected_exports(self) -> None:
        from general_ludd.reload import self_improve

        public = {n for n in dir(self_improve) if not n.startswith("_")}
        expected = {"ApplyResult", "SelfImprovementWorkflow"}
        assert expected <= public


class TestHardcodedFallback:
    def test_fallback_is_list_of_strings(self) -> None:
        assert isinstance(_HARDCODED_FALLBACK, list)
        assert len(_HARDCODED_FALLBACK) == 1
        assert _HARDCODED_FALLBACK[0] == "make test-unit"

    def test_fallback_is_immutable_semantic(self) -> None:
        before = _HARDCODED_FALLBACK[:]
        assert before == ["make test-unit"]


class TestResolveTestCommands:
    def test_returns_fallback_on_module_error(self) -> None:
        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            side_effect=RuntimeError("no"),
        ):
            result = _resolve_test_commands("/tmp/wt")
        assert result == ["make test-unit"]

    def test_returns_fallback_when_profile_has_no_test(self) -> None:
        fake = MagicMock()
        fake.has.return_value = False
        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=fake,
        ):
            result = _resolve_test_commands("/tmp/wt")
        assert result == ["make test-unit"]

    def test_returns_fallback_on_resolve_error(self) -> None:
        fake = MagicMock()
        fake.has.return_value = True
        fake.resolve_argv.side_effect = ValueError("bad args")
        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=fake,
        ):
            result = _resolve_test_commands("/tmp/wt")
        assert result == ["make test-unit"]

    def test_returns_resolved_command_when_profile_working(self) -> None:
        fake = MagicMock()
        fake.has.return_value = True
        fake.resolve_argv.return_value = ["pytest", "-x", "tests/"]
        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=fake,
        ):
            result = _resolve_test_commands("/tmp/wt")
        assert result == ["pytest -x tests/"]


class TestApplyResult:
    def test_construction_all_fields(self) -> None:
        ar = ApplyResult(
            todo_id="T1",
            applied=True,
            reload_needed=True,
            validation_passed=True,
        )
        assert ar.todo_id == "T1"
        assert ar.applied is True
        assert ar.reload_needed is True
        assert ar.validation_passed is True

    def test_construction_false_fields(self) -> None:
        ar = ApplyResult(
            todo_id="T2",
            applied=False,
            reload_needed=False,
            validation_passed=False,
        )
        assert ar.todo_id == "T2"
        assert ar.applied is False
        assert ar.reload_needed is False
        assert ar.validation_passed is False

    def test_is_dataclass(self) -> None:
        from dataclasses import is_dataclass

        assert is_dataclass(ApplyResult)


class TestSelfImprovementWorkflowInit:
    def test_init_default_config_dir(self) -> None:
        wf = SelfImprovementWorkflow()
        assert isinstance(wf._reload_manager, ReloadManager)
        assert wf._code_target is None
        assert wf._health_check is None
        assert wf._base_source_path is None
        assert wf._expected_sha256 is None
        assert wf._todos == {}

    def test_init_custom_config_dir(self) -> None:
        wf = SelfImprovementWorkflow(config_dir="other")
        assert isinstance(wf._reload_manager, ReloadManager)
        assert wf._code_target is None


class TestSetCodeTarget:
    def test_sets_minimal_fields(self) -> None:
        wf = SelfImprovementWorkflow()
        wf.set_code_target("mymod", "/tmp/candidate.py")
        assert wf._code_target == ("mymod", "/tmp/candidate.py")
        assert wf._health_check is None
        assert wf._base_source_path is None
        assert wf._expected_sha256 is None

    def test_sets_all_optional_fields(self) -> None:
        wf = SelfImprovementWorkflow()
        hc = lambda: True  # noqa: E731
        wf.set_code_target(
            "mymod",
            "/tmp/candidate.py",
            health_check=hc,
            base_source_path="/tmp/base.py",
            expected_sha256="abc123",
        )
        assert wf._code_target == ("mymod", "/tmp/candidate.py")
        assert wf._health_check is hc
        assert wf._base_source_path == "/tmp/base.py"
        assert wf._expected_sha256 == "abc123"

    def test_overwrites_previous_target(self) -> None:
        wf = SelfImprovementWorkflow()
        wf.set_code_target("a", "/tmp/a.py")
        wf.set_code_target("b", "/tmp/b.py")
        assert wf._code_target == ("b", "/tmp/b.py")


class TestCreateImprovementTodo:
    def test_creates_todo_with_required_fields(self) -> None:
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("Title", "Desc")
        assert todo["title"] == "Title"
        assert todo["description"] == "Desc"
        assert todo["status"] == "pending"
        assert isinstance(todo["todo_id"], str)
        assert todo["todo_id"].startswith("SI-")
        assert "created_at" in todo

    def test_todo_stored_in_internal_dict(self) -> None:
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("T", "D")
        assert wf._todos[str(todo["todo_id"])] is todo

    def test_creates_unique_ids(self) -> None:
        wf = SelfImprovementWorkflow()
        t1 = wf.create_improvement_todo("A", "B")
        t2 = wf.create_improvement_todo("C", "D")
        assert t1["todo_id"] != t2["todo_id"]


class TestValidateImprovement:
    def test_handles_missing_worktree(self, tmp_path) -> None:
        wf = SelfImprovementWorkflow()
        nonexistent = str(tmp_path / "does_not_exist")
        result = wf.validate_improvement(nonexistent)
        assert result.success is False
        assert result.failed_count == 1
        assert "could not run" in result.output

    def test_handles_empty_string_worktree(self) -> None:
        wf = SelfImprovementWorkflow()
        result = wf.validate_improvement("")
        assert result.success is False
        assert result.failed_count == 1

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_trailing_slash_stripped(self, mock_run: MagicMock, tmp_path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        base = tmp_path / "wts"
        base.mkdir()
        target = base / "wt-1"
        target.mkdir()
        wf = SelfImprovementWorkflow()
        result = wf.validate_improvement(str(target) + "/")
        assert result.success is True


class TestApplyImprovement:
    def test_fails_on_failed_validation(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.validation.runner import ValidationResult

        vr = ValidationResult(success=False, passed_count=0, failed_count=1, output="fail")
        ar = wf.apply_improvement("T1", vr)
        assert ar.applied is False
        assert ar.reload_needed is False
        assert ar.validation_passed is False

    def test_succeeds_on_passed_validation(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.validation.runner import ValidationResult

        vr = ValidationResult(success=True, passed_count=3, failed_count=0, output="3 passed")
        ar = wf.apply_improvement("T1", vr)
        assert ar.applied is True
        assert ar.reload_needed is True
        assert ar.validation_passed is True

    def test_updates_todo_status_when_exists(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.validation.runner import ValidationResult

        todo = wf.create_improvement_todo("T", "D")
        tid = str(todo["todo_id"])
        vr = ValidationResult(success=True, passed_count=1, failed_count=0, output="")
        wf.apply_improvement(tid, vr)
        assert wf._todos[tid]["status"] == "applied"

    def test_does_not_crash_on_unknown_todo_id(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.validation.runner import ValidationResult

        vr = ValidationResult(success=True, passed_count=1, failed_count=0, output="")
        ar = wf.apply_improvement("nonexistent", vr)
        assert ar.applied is True


class TestReloadIfNeeded:
    def test_no_reload_when_not_needed(self) -> None:
        wf = SelfImprovementWorkflow()
        ar = ApplyResult(todo_id="T1", applied=False, reload_needed=False, validation_passed=False)
        result = wf.reload_if_needed(ar)
        assert result.status == "pending"
        assert "not needed" in result.message

    def test_in_memory_fallback_when_no_code_target(self) -> None:
        wf = SelfImprovementWorkflow()
        ar = ApplyResult(todo_id="T1", applied=True, reload_needed=True, validation_passed=True)
        result = wf.reload_if_needed(ar)
        assert result.status == "no_op"
        assert result.reload_type == ReloadType.WORKER_CODE

    def test_hot_rotation_success_path(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.reload.hot_reloader import ReloadResult as HotReloadResult

        hot_result = HotReloadResult(
            success=True,
            scope="module",
            details={},
        )
        wf._hot_reloader.reload_code_module = MagicMock(return_value=hot_result)
        wf.set_code_target("mymod", "/tmp/candidate.py")
        ar = ApplyResult(todo_id="T1", applied=True, reload_needed=True, validation_passed=True)
        result = wf.reload_if_needed(ar)
        assert result.status == "success"
        assert result.reload_type == ReloadType.WORKER_CODE
        assert "Hot-rotated" in result.message

    def test_hot_rotation_failure_path(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.reload.hot_reloader import ReloadResult as HotReloadResult

        hot_result = HotReloadResult(
            success=False,
            scope="module",
            details={},
            error="import failed",
        )
        wf._hot_reloader.reload_code_module = MagicMock(return_value=hot_result)
        wf.set_code_target("mymod", "/tmp/candidate.py")
        ar = ApplyResult(todo_id="T1", applied=True, reload_needed=True, validation_passed=True)
        result = wf.reload_if_needed(ar)
        assert result.status == "failed"
        assert "import failed" in result.message

    def test_hot_rotation_rollback_message(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.reload.hot_reloader import ReloadResult as HotReloadResult

        hot_result = HotReloadResult(
            success=False,
            scope="module",
            details={"rolled_back": True},
            error="health gate failed",
        )
        wf._hot_reloader.reload_code_module = MagicMock(return_value=hot_result)
        wf.set_code_target("mymod", "/tmp/candidate.py")
        ar = ApplyResult(todo_id="T1", applied=True, reload_needed=True, validation_passed=True)
        result = wf.reload_if_needed(ar)
        assert result.status == "failed"
        assert "Rolled back" in result.message or "rolled back" in result.message

    def test_hot_rotation_unexpected_exception(self) -> None:
        wf = SelfImprovementWorkflow()
        wf._hot_reloader.reload_code_module = MagicMock(side_effect=RuntimeError("boom"))
        wf.set_code_target("mymod", "/tmp/candidate.py")
        ar = ApplyResult(todo_id="T1", applied=True, reload_needed=True, validation_passed=True)
        result = wf.reload_if_needed(ar)
        assert result.status == "failed"
        assert "unexpectedly" in result.message

    def test_hot_rotation_passes_all_params(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.reload.hot_reloader import ReloadResult as HotReloadResult

        mock_reload = MagicMock(
            return_value=HotReloadResult(success=True, scope="module", details={})
        )
        wf._hot_reloader.reload_code_module = mock_reload
        hc = lambda: True  # noqa: E731
        wf.set_code_target(
            "mymod",
            "/tmp/candidate.py",
            health_check=hc,
            base_source_path="/tmp/base.py",
            expected_sha256="abc123",
        )
        ar = ApplyResult(todo_id="T1", applied=True, reload_needed=True, validation_passed=True)
        wf.reload_if_needed(ar)
        mock_reload.assert_called_once_with(
            module_name="mymod",
            candidate_source_path="/tmp/candidate.py",
            health_check=hc,
            base_source_path="/tmp/base.py",
            expected_sha256="abc123",
        )


class TestSelfImprovementWorkflowIntegration:
    def test_full_flow_failure_to_noop(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.validation.runner import ValidationResult

        vr = ValidationResult(success=False, passed_count=0, failed_count=1, output="fail")
        ar = wf.apply_improvement("T1", vr)
        assert ar.validation_passed is False
        assert ar.reload_needed is False

        rr = wf.reload_if_needed(ar)
        assert rr.status == "pending"

    def test_full_flow_success_to_hot_rotate(self) -> None:
        wf = SelfImprovementWorkflow()
        from general_ludd.reload.hot_reloader import ReloadResult as HotReloadResult
        from general_ludd.validation.runner import ValidationResult

        mock_reload = MagicMock(
            return_value=HotReloadResult(success=True, scope="module", details={})
        )
        wf._hot_reloader.reload_code_module = mock_reload
        wf.set_code_target("m", "/tmp/c.py")

        vr = ValidationResult(success=True, passed_count=5, failed_count=0, output="5 passed")
        ar = wf.apply_improvement("T1", vr)
        assert ar.validation_passed is True
        assert ar.reload_needed is True

        rr = wf.reload_if_needed(ar)
        assert rr.status == "success"
        mock_reload.assert_called_once()
