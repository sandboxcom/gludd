"""Dogfood runner — seeds todos and runs smoke tasks for self-testing."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, field_validator

from general_ludd.dogfood.sprint_parser import parse_sprint_markdown


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
            import subprocess
            result = subprocess.run(
                ["ansible-playbook", "--syntax-check", f"playbooks/{task_name}.yml"],
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
