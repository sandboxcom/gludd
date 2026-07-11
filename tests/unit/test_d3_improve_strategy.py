"""Tests for D3 improve-strategy: _resolve_test_commands and validate_improvement.

Covers: profile-driven test command resolution, fallback on profile failure,
integration with ValidationRunner in validate_improvement.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.project_runner.profile import ProjectProfileError
from general_ludd.reload.self_improve import (
    SelfImprovementWorkflow,
    _resolve_test_commands,
)
from general_ludd.validation.runner import ValidationResult

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_profile(commands: dict[str, str] | None = None) -> MagicMock:
    profile = MagicMock()
    profile.commands = commands or {}
    return profile


# ── _resolve_test_commands ──────────────────────────────────────────────────


class TestResolveTestCommands:
    """Tests for _resolve_test_commands — profile-driven test command resolution."""

    def test_returns_detected_command_from_makefile_profile(self) -> None:
        profile = _make_mock_profile({"test": "make test"})
        profile.has.return_value = True
        profile.resolve_argv.return_value = ["make", "test"]

        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=profile,
        ):
            result = _resolve_test_commands("/fake/worktree")

        assert result == ["make test"]

    def test_returns_detected_command_from_python_profile(self) -> None:
        profile = _make_mock_profile({"test": "pytest -q"})
        profile.has.return_value = True
        profile.resolve_argv.return_value = ["pytest", "-q"]

        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=profile,
        ):
            result = _resolve_test_commands("/fake/worktree")

        assert result == ["pytest -q"]

    def test_falls_back_when_load_project_profile_raises(self) -> None:
        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            side_effect=ProjectProfileError("no project.yml found"),
        ):
            result = _resolve_test_commands("/fake/worktree")

        assert result == ["make test-unit"]

    def test_falls_back_when_load_project_profile_raises_generic_exception(self) -> None:
        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            side_effect=OSError("disk failure"),
        ):
            result = _resolve_test_commands("/fake/worktree")

        assert result == ["make test-unit"]

    def test_falls_back_when_profile_has_no_test_command(self) -> None:
        profile = _make_mock_profile()
        profile.has.return_value = False

        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=profile,
        ):
            result = _resolve_test_commands("/fake/worktree")

        assert result == ["make test-unit"]

    def test_falls_back_when_resolve_argv_raises(self) -> None:
        profile = _make_mock_profile({"test": "unsafe | command"})
        profile.has.return_value = True
        profile.resolve_argv.side_effect = ProjectProfileError(
            "command 'test' contains shell metacharacters"
        )

        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=profile,
        ):
            result = _resolve_test_commands("/fake/worktree")

        assert result == ["make test-unit"]


# ── validate_improvement ────────────────────────────────────────────────────


class TestValidateImprovement:
    """Tests for SelfImprovementWorkflow.validate_improvement — D3 strategy."""

    def test_passes_resolved_commands_to_validation_runner(self, tmp_path: str) -> None:
        profile = _make_mock_profile({"test": "make test"})
        profile.has.return_value = True
        profile.resolve_argv.return_value = ["make", "test"]

        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            return_value=profile,
        ), patch(
            "general_ludd.reload.self_improve.ValidationRunner"
        ) as MockRunner:
            mock_instance = MagicMock()
            mock_instance.run_validation.return_value = ValidationResult(
                success=True,
                passed_count=1,
                failed_count=0,
                output="all ok",
                failures=[],
            )
            MockRunner.return_value = mock_instance

            workflow = SelfImprovementWorkflow()
            workflow.validate_improvement(str(tmp_path))

            MockRunner.assert_called_once()
            call_kwargs = MockRunner.call_args.kwargs
            assert call_kwargs["test_commands"] == ["make test"]

    def test_returns_validation_failure_for_missing_worktree(self) -> None:
        workflow = SelfImprovementWorkflow()
        result = workflow.validate_improvement("/nonexistent/path/to/worktree")

        assert isinstance(result, ValidationResult)
        assert result.success is False
        assert result.failed_count >= 1
        assert result.output
        assert "validation could not run" in result.output

    def test_returns_validation_failure_for_file_not_directory(
        self, tmp_path: str
    ) -> None:
        bad_path = tmp_path / "not_a_dir"
        bad_path.write_text("I am a file, not a directory")

        with patch(
            "general_ludd.reload.self_improve.load_project_profile",
            side_effect=ProjectProfileError("no project.yml found"),
        ):
            workflow = SelfImprovementWorkflow()
            result = workflow.validate_improvement(str(bad_path))

        assert isinstance(result, ValidationResult)
        assert result.success is False
