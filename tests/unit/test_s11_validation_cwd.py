"""S.11: ValidationRunner subprocess cwd confinement (D7/CA-validation).

Proves that run_validation() confines subprocess cwd to the expected root,
and that the expected_worktree_root parameter is required (not optional).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.validation.runner import CommandValidationError, ValidationRunner


def _passing_proc() -> MagicMock:
    return MagicMock(returncode=0, stdout="1 passed", stderr="")


class TestExpectedWorktreeRootRequired:
    def test_constructor_requires_expected_worktree_root(self) -> None:
        with pytest.raises(TypeError):
            ValidationRunner(
                todo_id="TODO-1",
                worktree_path="/tmp/worktree",
                test_commands=["make test"],
            )

    def test_constructor_accepts_expected_worktree_root(self) -> None:
        runner = ValidationRunner(
            todo_id="TODO-2",
            worktree_path="/tmp/worktree",
            test_commands=["make test"],
            expected_worktree_root="/tmp",
        )
        assert runner._expected_worktree_root == "/tmp"

    def test_expected_root_is_not_none(self, tmp_path) -> None:
        base = tmp_path / "worktrees"
        base.mkdir()
        target = base / "wt-1"
        target.mkdir()
        runner = ValidationRunner(
            todo_id="TODO-3",
            worktree_path=str(target),
            test_commands=["make test"],
            expected_worktree_root=str(base),
        )
        assert runner._expected_worktree_root is not None


class TestCwdConfinementInRunValidation:
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_cwd_is_confined_to_allowed_root(
        self, mock_run: MagicMock, tmp_path
    ) -> None:
        mock_run.return_value = _passing_proc()
        base = tmp_path / "worktrees"
        base.mkdir()
        target = base / "wt-1"
        target.mkdir()

        runner = ValidationRunner(
            todo_id="TODO-confine",
            worktree_path=str(target),
            test_commands=["make test"],
            expected_worktree_root=str(base),
        )
        result = runner.run_validation()
        assert result.success is True
        _args, kwargs = mock_run.call_args
        real_cwd = kwargs["cwd"]
        assert real_cwd.startswith(str(base.resolve()))

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_cwd_does_not_escape_root(
        self, mock_run: MagicMock, tmp_path
    ) -> None:
        mock_run.return_value = _passing_proc()
        base = tmp_path / "worktrees"
        base.mkdir()
        target = base / "wt-1"
        target.mkdir()

        runner = ValidationRunner(
            todo_id="TODO-no-escape",
            worktree_path=str(target),
            test_commands=["make test"],
            expected_worktree_root=str(base),
        )
        runner.run_validation()
        _args, kwargs = mock_run.call_args
        real_cwd = kwargs["cwd"]
        base_real = str(base.resolve())
        assert real_cwd == base_real or real_cwd.startswith(base_real + "/")

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_always_confines_before_subprocess_no_bypass(
        self, mock_run: MagicMock, tmp_path
    ) -> None:
        mock_run.return_value = _passing_proc()
        base = tmp_path / "worktrees"
        base.mkdir()
        target = base / "wt-1"
        target.mkdir()

        runner = ValidationRunner(
            todo_id="TODO-always",
            worktree_path=str(target),
            test_commands=["make test"],
            expected_worktree_root=str(base),
        )
        runner.run_validation()
        assert mock_run.call_count == 1
        _args, kwargs = mock_run.call_args
        resolved_cwd = kwargs["cwd"]
        assert str(base.resolve()) in resolved_cwd


class TestSymlinkEscapeRejectedAtConstruction:
    def test_symlink_outside_root_is_rejected(self, tmp_path) -> None:
        base = tmp_path / "worktrees"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = base / "wt-evil"
        link.symlink_to(outside)

        with pytest.raises(CommandValidationError):
            ValidationRunner(
                todo_id="TODO-escape",
                worktree_path=str(link),
                test_commands=["make test"],
                expected_worktree_root=str(base),
            )

    def test_traversal_outside_root_is_rejected(self, tmp_path) -> None:
        base = tmp_path / "worktrees"
        base.mkdir()

        with pytest.raises(CommandValidationError):
            ValidationRunner(
                todo_id="TODO-traverse",
                worktree_path=str(base / ".." / "etc"),
                test_commands=["make test"],
                expected_worktree_root=str(base),
            )
