"""Validation runner — executes test commands and produces results."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any

from general_ludd.worktree.core import confine_worktree_path

logger = logging.getLogger(__name__)

# Shell metacharacters that imply the command author expected shell semantics
# that this runner deliberately does NOT provide (we never use shell=True). A
# string containing any of these is rejected outright rather than being
# silently mis-executed by shlex.split — e.g. "pytest; rm -rf /" would have run
# only "pytest" while dropping the destructive tail, masking an injection.
_SHELL_METACHARS = frozenset(";&|<>`$()\n\r")

# Allowlist of expected test runners. The config supplies command strings, so
# the leading argv token (the binary to exec) is constrained to known-safe
# runners unless the caller explicitly opts out via enforce_runner_allowlist.
_DEFAULT_RUNNER_ALLOWLIST = frozenset({"pytest", "uv", "make", "python", "python3"})


class CommandValidationError(ValueError):
    """Raised when a config-supplied test command fails safety validation."""


def _validate_worktree_path(path: str, *, expected_root: str | None = None) -> str:
    """Validate a worktree path before use as subprocess cwd (D7).

    Rejects:
    * Paths beginning with ``-`` (would be parsed as a CLI option).
    * Non-absolute paths (relative paths allow ``../`` traversal).

    When ``expected_root`` is supplied, delegates to
    ``worktree.core.confine_worktree_path`` for the full fail-closed guard:
    symlinks are resolved (``os.path.realpath``) and the resolved path must
    be ``expected_root`` itself or a descendant of it — this catches ``..``
    traversal AND a symlink planted at (or under) ``path`` that resolves
    somewhere outside the expected root. A caller that does not know its
    root falls back to the weaker but still meaningful absolute-path guard.

    Returns the (possibly symlink-resolved, when ``expected_root`` is given)
    path on success.
    """
    if not path or not path.strip():
        raise CommandValidationError("worktree_path must not be empty")
    if path.startswith("-"):
        raise CommandValidationError(
            f"worktree_path begins with '-' (would be parsed as a CLI option): {path!r}"
        )
    if not path.startswith("/"):
        raise CommandValidationError(
            f"worktree_path must be an absolute path (got relative path): {path!r}"
        )
    if expected_root is not None:
        try:
            return confine_worktree_path(path, expected_root)
        except ValueError as exc:
            # Fail closed: a path that escapes the expected root (traversal or
            # symlink escape) is a validation failure, not a crash.
            raise CommandValidationError(
                f"worktree_path escapes expected root {expected_root!r}: {exc}"
            ) from exc
    return path


def _validate_command(
    cmd: str, *, enforce_allowlist: bool, allowlist: frozenset[str]
) -> list[str]:
    """Validate and tokenize a config-supplied command into an argv list.

    Rejects shell-metachar-laden strings (which shlex.split would mis-handle)
    and, unless opted out, constrains the runner binary to an allowlist. The
    returned argv is always passed to subprocess with shell=False.
    """
    if not cmd or not cmd.strip():
        raise CommandValidationError("empty test command")

    bad = sorted(set(cmd) & _SHELL_METACHARS)
    if bad:
        raise CommandValidationError(
            f"test command contains shell metacharacter(s) {bad!r}; "
            f"shell semantics are not supported (no shell=True): {cmd!r}"
        )

    try:
        argv = shlex.split(cmd)
    except ValueError as exc:  # unbalanced quotes, etc.
        raise CommandValidationError(f"unparseable test command {cmd!r}: {exc}") from exc

    if not argv:
        raise CommandValidationError(f"test command tokenized to nothing: {cmd!r}")

    if enforce_allowlist:
        # Compare on the bare basename so "/usr/bin/python" still matches.
        runner = argv[0].rsplit("/", 1)[-1]
        if runner not in allowlist:
            raise CommandValidationError(
                f"test runner {runner!r} is not in the allowlist "
                f"{sorted(allowlist)!r}; pass enforce_runner_allowlist=False "
                f"to opt out: {cmd!r}"
            )

    return argv


@dataclass
class ValidationResult:
    success: bool
    passed_count: int
    failed_count: int
    output: str
    failures: list[str] = field(default_factory=list)
    child_todos: list[dict[str, Any]] = field(default_factory=list)


class ValidationRunner:
    def __init__(
        self,
        todo_id: str,
        worktree_path: str,
        test_commands: list[str],
        *,
        enforce_runner_allowlist: bool = True,
        runner_allowlist: frozenset[str] = _DEFAULT_RUNNER_ALLOWLIST,
        expected_worktree_root: str | None = None,
    ) -> None:
        self.todo_id = todo_id
        self.worktree_path = _validate_worktree_path(
            worktree_path, expected_root=expected_worktree_root
        )
        self.test_commands = test_commands
        self.enforce_runner_allowlist = enforce_runner_allowlist
        self.runner_allowlist = runner_allowlist

    def run_validation(self) -> ValidationResult:
        combined_stdout = ""
        all_failures: list[str] = []
        total_passed = 0
        total_failed = 0

        # Validate ALL commands up front so a bad command never causes a
        # partial run (fail-closed): we reject the batch before exec'ing any.
        argvs = [
            _validate_command(
                cmd,
                enforce_allowlist=self.enforce_runner_allowlist,
                allowlist=self.runner_allowlist,
            )
            for cmd in self.test_commands
        ]

        for argv in argvs:
            # argv is a validated list (never a shell string); shell=False.
            proc = subprocess.run(
                argv,
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
                shell=False,
            )
            combined_stdout += proc.stdout + "\n"
            passed, failed, failures = _parse_pytest_output(proc.stdout, proc.returncode)
            total_passed += passed
            total_failed += failed
            all_failures.extend(failures)

        return ValidationResult(
            success=total_failed == 0 and total_passed > 0,
            passed_count=total_passed,
            failed_count=total_failed,
            output=combined_stdout.strip(),
            failures=all_failures,
        )

    def create_child_todos_for_failures(self, result: ValidationResult) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        if result.failures:
            for failure in result.failures:
                children.append({
                    "parent_todo_id": self.todo_id,
                    "title": f"Fix failing test: {failure}",
                    "description": failure,
                    "category": "test_failure",
                    "status": "backlog",
                })
        elif result.passed_count == 0 and not result.failures:
            children.append({
                "parent_todo_id": self.todo_id,
                "title": "Add missing tests",
                "description": "No tests were found or executed",
                "category": "missing_tests",
                "status": "backlog",
            })
        return children


_FAILED_RE = re.compile(r"FAILED\s+(\S+)")
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_COUNT_RE = re.compile(r"(\d+)\s+failed")


def _parse_pytest_output(output: str, returncode: int) -> tuple[int, int, list[str]]:
    if returncode == 0:
        m = _PASSED_RE.search(output)
        passed = int(m.group(1)) if m else 0
        return passed, 0, []

    failures = _FAILED_RE.findall(output)
    passed_m = _PASSED_RE.search(output)
    failed_m = _FAILED_COUNT_RE.search(output)
    passed = int(passed_m.group(1)) if passed_m else 0
    failed = int(failed_m.group(1)) if failed_m else len(failures)
    return passed, failed, failures
