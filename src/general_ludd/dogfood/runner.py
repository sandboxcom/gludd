"""Dogfood runner — seeds todos and runs smoke tasks for self-testing."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, field_validator

from general_ludd.dogfood.sprint_parser import parse_sprint_markdown

# A smoke-task name is interpolated into the playbook path
# (``playbooks/<task_name>.yml``) and handed to ``ansible-playbook`` as an argv
# token.  It is caller-derived, so it must be confined: no path separators /
# ``..`` traversal (which would escape ``playbooks/``), no leading dash (which
# ansible-playbook would parse as an option), and no shell metacharacters
# (defence-in-depth even though we never use a shell / shell=True).
_SAFE_TASK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHELL_META_CHARS = set(";|&$`><(){}[]!*?~#'\" \t\n\r\\")


def _validate_task_name(task_name: str) -> str:
    """Return ``task_name`` if it is a confined, injection-safe playbook stem.

    Raises ``ValueError`` otherwise.  Rejects: non-string, empty, path
    separators, ``..`` traversal, leading dash, and any shell metacharacter.
    """
    if not isinstance(task_name, str) or not task_name:
        raise ValueError("task_name must be a non-empty string")
    if "/" in task_name or "\\" in task_name:
        raise ValueError(f"task_name must not contain path separators: {task_name!r}")
    if ".." in task_name:
        raise ValueError(f"task_name must not contain traversal: {task_name!r}")
    if task_name.startswith("-"):
        raise ValueError(f"task_name must not start with a dash: {task_name!r}")
    if any(ch in _SHELL_META_CHARS for ch in task_name):
        raise ValueError(f"task_name must not contain shell metacharacters: {task_name!r}")
    if not _SAFE_TASK_NAME.match(task_name):
        raise ValueError(f"task_name is not a safe playbook stem: {task_name!r}")
    return task_name


class DogfoodConfig(BaseModel):
    repo_root: str
    target_repo: str
    runtime_profile: str
    model_profile: str
    auto_commit: bool = True

    @field_validator("repo_root", "target_repo", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v


@dataclass
class SmokeTaskResult:
    task_name: str
    success: bool
    duration_seconds: float
    output: str


@dataclass
class DogfoodProfile:
    repo_root: str
    target_repo: str
    runtime_mode: str
    model_profiles: list[str]
    enabled: bool


class DogfoodRunner:
    def __init__(self, config: DogfoodConfig) -> None:
        self.config = config

    def seed_todos_from_sprint(self, sprint_path: str) -> list[dict[str, Any]]:
        items = parse_sprint_markdown(sprint_path)
        todos: list[dict[str, Any]] = []
        for item in items:
            for task in item.tasks:
                todos.append({
                    "description": task,
                    "source": "sprint",
                    "objective_number": item.objective_number,
                    "objective_title": item.title,
                })
        return todos

    def seed_todos_from_gap_analysis(self, gap_report: Any) -> list[dict[str, Any]]:
        todos: list[dict[str, Any]] = []
        for gap in gap_report.gaps:
            todos.append({
                "description": gap.description,
                "source": "gap_analysis",
                "category": gap.category,
                "severity": gap.severity,
                "suggested_action": gap.suggested_action,
            })
        return todos

    def seed_todos_from_test_failures(self, test_output: str) -> list[dict[str, Any]]:
        pattern = re.compile(r"FAILED\s+(\S+::\S+)")
        matches = pattern.findall(test_output)
        todos: list[dict[str, Any]] = []
        for match in matches:
            todos.append({
                "description": f"Fix failing test: {match}",
                "source": "test_failure",
                "test_id": match,
            })
        return todos

    def run_smoke_task(self, task_name: str) -> SmokeTaskResult:
        start = time.time()
        try:
            # Confine the caller-derived task name before it reaches argv: a
            # bad name (traversal / leading-dash / metachar) must fail closed
            # WITHOUT spawning a subprocess.
            safe_name = _validate_task_name(task_name)
            result = subprocess.run(
                ["ansible-playbook", "--syntax-check", f"playbooks/{safe_name}.yml"],
                capture_output=True,
                text=True,
                cwd=self.config.repo_root,
            )
            elapsed = time.time() - start
            return SmokeTaskResult(
                task_name=task_name,
                success=result.returncode == 0,
                duration_seconds=elapsed,
                output=result.stdout + result.stderr,
            )
        except Exception as e:
            elapsed = time.time() - start
            return SmokeTaskResult(
                task_name=task_name,
                success=False,
                duration_seconds=elapsed,
                output=str(e),
            )

    def create_dogfood_profile(self) -> DogfoodProfile:
        return DogfoodProfile(
            repo_root=self.config.repo_root,
            target_repo=self.config.target_repo,
            runtime_mode=self.config.runtime_profile,
            model_profiles=[self.config.model_profile],
            enabled=True,
        )
