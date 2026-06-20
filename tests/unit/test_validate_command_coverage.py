"""Coverage tests for _validate_command and ValidationRunner.run_validation.

Targets:
  src/general_ludd/validation/runner.py
    _validate_command  — shell-metachar rejection, allowlist enforcement,
                         empty/whitespace/unbalanced-quote rejection, happy paths
    run_validation     — all commands validated before any subprocess call
                         (fail-closed); subprocess.run called shell=False + cwd
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call, patch

from general_ludd.validation.runner import (
    CommandValidationError,
    ValidationRunner,
    _validate_command,
    _DEFAULT_RUNNER_ALLOWLIST,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vc(cmd: str, *, enforce: bool = True, allowlist: frozenset[str] = _DEFAULT_RUNNER_ALLOWLIST) -> list[str]:
    return _validate_command(cmd, enforce_allowlist=enforce, allowlist=allowlist)


# ---------------------------------------------------------------------------
# Empty / whitespace
# ---------------------------------------------------------------------------

class TestValidateCommandEmpty:
    def test_empty_string_raises(self) -> None:
        with pytest.raises(CommandValidationError, match="empty test command"):
            _vc("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(CommandValidationError, match="empty test command"):
            _vc("   ")

    def test_tab_only_raises(self) -> None:
        with pytest.raises(CommandValidationError, match="empty test command"):
            _vc("\t")


# ---------------------------------------------------------------------------
# Shell metacharacter rejection
# ---------------------------------------------------------------------------

class TestValidateCommandMetachars:
    """Each shell metachar in _SHELL_METACHARS must be independently rejected."""

    @pytest.mark.parametrize("char", [";", "&", "|", "<", ">", "`", "$", "(", ")", "\n", "\r"])
    def test_metachar_rejected(self, char: str) -> None:
        cmd = f"pytest tests/{char}foo"
        with pytest.raises(CommandValidationError, match="shell metacharacter"):
            _vc(cmd)

    def test_slash_is_not_a_metachar(self) -> None:
        """'/' must NOT be rejected — it appears in paths like /usr/local/bin/pytest."""
        argv = _vc("/usr/local/bin/pytest tests/", enforce=False)
        assert argv[0] == "/usr/local/bin/pytest"

    def test_injection_semicolon(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest; rm -rf /")

    def test_injection_pipe(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest | tee out.txt")

    def test_injection_subshell(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest $(whoami)")

    def test_injection_backtick(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest `id`")

    def test_injection_dollar(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest $HOME/tests")

    def test_injection_redirect_out(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest > /tmp/out")

    def test_injection_redirect_in(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest < /tmp/in")

    def test_injection_newline(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest\nrm -rf /")

    def test_injection_carriage_return(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest\rrm -rf /")

    def test_ampersand_background(self) -> None:
        with pytest.raises(CommandValidationError):
            _vc("pytest tests/ &")


# ---------------------------------------------------------------------------
# Unbalanced quotes (shlex)
# ---------------------------------------------------------------------------

class TestValidateCommandQuotes:
    def test_unbalanced_single_quote_raises(self) -> None:
        with pytest.raises(CommandValidationError, match="unparseable"):
            _vc("pytest tests/ --name 'foo")

    def test_unbalanced_double_quote_raises(self) -> None:
        with pytest.raises(CommandValidationError, match="unparseable"):
            _vc('pytest tests/ --name "foo')

    def test_balanced_double_quotes_ok(self) -> None:
        argv = _vc('pytest tests/ -k "my test"', enforce=False)
        assert "my test" in argv


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------

class TestValidateCommandAllowlist:
    def test_pytest_bare_accepted(self) -> None:
        argv = _vc("pytest tests/")
        assert argv[0] == "pytest"

    def test_uv_accepted(self) -> None:
        argv = _vc("uv run pytest")
        assert argv[0] == "uv"

    def test_make_accepted(self) -> None:
        argv = _vc("make test")
        assert argv[0] == "make"

    def test_python_accepted(self) -> None:
        argv = _vc("python -m pytest tests/")
        assert argv[0] == "python"

    def test_python3_accepted(self) -> None:
        argv = _vc("python3 -m pytest tests/")
        assert argv[0] == "python3"

    def test_absolute_path_basename_matched(self) -> None:
        """'/usr/local/bin/pytest x' should pass because basename is 'pytest'."""
        argv = _vc("/usr/local/bin/pytest tests/unit")
        assert argv[0] == "/usr/local/bin/pytest"

    def test_uv_run_pytest_compound(self) -> None:
        argv = _vc("uv run pytest tests/")
        assert argv[:2] == ["uv", "run"]

    def test_bash_rejected_when_enforce_true(self) -> None:
        with pytest.raises(CommandValidationError, match="allowlist"):
            _vc("bash run_tests.sh", enforce=True)

    def test_curl_rejected_when_enforce_true(self) -> None:
        with pytest.raises(CommandValidationError, match="allowlist"):
            _vc("curl http://example.com", enforce=True)

    def test_sh_rejected_when_enforce_true(self) -> None:
        with pytest.raises(CommandValidationError, match="allowlist"):
            _vc("sh -c 'pytest'", enforce=True)

    def test_enforce_false_bypasses_allowlist(self) -> None:
        """enforce_allowlist=False must allow arbitrary runners."""
        argv = _vc("bash run_tests.sh", enforce=False)
        assert argv[0] == "bash"

    def test_enforce_false_curl_allowed(self) -> None:
        argv = _vc("curl http://localhost:8000/healthz", enforce=False)
        assert argv[0] == "curl"

    def test_custom_allowlist_accepted(self) -> None:
        argv = _vc("myrunner tests/", enforce=True, allowlist=frozenset({"myrunner"}))
        assert argv[0] == "myrunner"

    def test_custom_allowlist_rejects_pytest(self) -> None:
        """If the allowlist doesn't include pytest, it should reject it."""
        with pytest.raises(CommandValidationError, match="allowlist"):
            _vc("pytest tests/", enforce=True, allowlist=frozenset({"myrunner"}))


# ---------------------------------------------------------------------------
# Happy-path tokenisation
# ---------------------------------------------------------------------------

class TestValidateCommandHappyPaths:
    def test_pytest_with_path(self) -> None:
        argv = _vc("pytest tests/unit/test_foo.py")
        assert argv == ["pytest", "tests/unit/test_foo.py"]

    def test_pytest_with_flag(self) -> None:
        argv = _vc("pytest tests/ -v")
        assert argv == ["pytest", "tests/", "-v"]

    def test_uv_run_pytest(self) -> None:
        argv = _vc("uv run pytest tests/")
        assert argv == ["uv", "run", "pytest", "tests/"]

    def test_absolute_pytest_path_with_arg(self) -> None:
        argv = _vc("/usr/local/bin/pytest tests/unit")
        assert argv == ["/usr/local/bin/pytest", "tests/unit"]


# ---------------------------------------------------------------------------
# run_validation — fail-closed: bad cmd → zero subprocess calls
# ---------------------------------------------------------------------------

class TestRunValidationFailClosed:
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_bad_cmd_in_batch_no_subprocess_called(self, mock_run: MagicMock) -> None:
        """If any command fails validation, subprocess.run must NOT be called at all."""
        runner = ValidationRunner(
            todo_id="TODO-X",
            worktree_path="/tmp/wt",
            test_commands=["pytest tests/", "bash inject.sh"],  # second cmd bad
            enforce_runner_allowlist=True,
        )
        with pytest.raises(CommandValidationError):
            runner.run_validation()
        assert mock_run.call_count == 0

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_metachar_cmd_no_subprocess_called(self, mock_run: MagicMock) -> None:
        runner = ValidationRunner(
            todo_id="TODO-Y",
            worktree_path="/tmp/wt",
            test_commands=["pytest; rm -rf /"],
        )
        with pytest.raises(CommandValidationError):
            runner.run_validation()
        assert mock_run.call_count == 0

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_first_cmd_bad_second_not_run(self, mock_run: MagicMock) -> None:
        """Even the first-in-batch cmd is validated before any exec."""
        runner = ValidationRunner(
            todo_id="TODO-Z",
            worktree_path="/tmp/wt",
            test_commands=["curl http://evil.com", "pytest tests/"],
            enforce_runner_allowlist=True,
        )
        with pytest.raises(CommandValidationError):
            runner.run_validation()
        assert mock_run.call_count == 0


# ---------------------------------------------------------------------------
# run_validation — subprocess called shell=False with correct cwd
# ---------------------------------------------------------------------------

class TestRunValidationSubprocessArgs:
    @patch("general_ludd.validation.runner.subprocess.run")
    def test_shell_false_and_cwd(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        worktree = "/tmp/my_worktree"
        runner = ValidationRunner(
            todo_id="TODO-1",
            worktree_path=worktree,
            test_commands=["pytest tests/"],
        )
        runner.run_validation()
        assert mock_run.call_count == 1
        _, kwargs = mock_run.call_args
        assert kwargs.get("shell") is False or mock_run.call_args[1].get("shell") is False or (
            # positional check: subprocess.run(argv, cwd=..., shell=False, ...)
            mock_run.call_args[1].get("shell", True) is False
        )
        assert mock_run.call_args[1]["cwd"] == worktree

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_shell_false_kwarg_explicit(self, mock_run: MagicMock) -> None:
        """shell kwarg must be explicitly False, not just omitted."""
        mock_run.return_value = MagicMock(returncode=0, stdout="3 passed", stderr="")
        runner = ValidationRunner(
            todo_id="TODO-2",
            worktree_path="/tmp/wt2",
            test_commands=["uv run pytest tests/"],
        )
        runner.run_validation()
        kwargs = mock_run.call_args[1]
        assert kwargs["shell"] is False

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_argv_list_not_string(self, mock_run: MagicMock) -> None:
        """First positional arg to subprocess.run must be a list, not a string."""
        mock_run.return_value = MagicMock(returncode=0, stdout="1 passed", stderr="")
        runner = ValidationRunner(
            todo_id="TODO-3",
            worktree_path="/tmp/wt3",
            test_commands=["pytest tests/unit"],
        )
        runner.run_validation()
        args, _ = mock_run.call_args
        assert isinstance(args[0], list)

    @patch("general_ludd.validation.runner.subprocess.run")
    def test_multiple_valid_commands_all_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="2 passed", stderr="")
        runner = ValidationRunner(
            todo_id="TODO-4",
            worktree_path="/tmp/wt4",
            test_commands=["pytest tests/unit", "pytest tests/integration"],
        )
        runner.run_validation()
        assert mock_run.call_count == 2
