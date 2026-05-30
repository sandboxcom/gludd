"""Validation runner — executes test commands and produces results."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    success: bool
    passed_count: int
    failed_count: int
    output: str
    failures: list[str] = field(default_factory=list)
    child_todos: list[dict] = field(default_factory=list)


class ValidationRunner:
    def __init__(self, todo_id: str, worktree_path: str, test_commands: list[str]) -> None:
        self.todo_id = todo_id
        self.worktree_path = worktree_path
        self.test_commands = test_commands

    def run_validation(self) -> ValidationResult:
        combined_stdout = ""
        all_failures: list[str] = []
        total_passed = 0
        total_failed = 0

        for cmd in self.test_commands:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
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

    def create_child_todos_for_failures(self, result: ValidationResult) -> list[dict]:
        children: list[dict] = []
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
