"""Self-improvement workflow — validate, apply, and reload harness improvements."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agentic_harness.reload.manager import ReloadManager, ReloadResult, ReloadType
from agentic_harness.validation.runner import ValidationResult, ValidationRunner


@dataclass
class ApplyResult:
    todo_id: str
    applied: bool
    reload_needed: bool
    validation_passed: bool


class SelfImprovementWorkflow:
    def __init__(self) -> None:
        self._reload_manager = ReloadManager()
        self._todos: dict[str, dict[str, Any]] = {}

    def create_improvement_todo(self, title: str, description: str) -> dict[str, Any]:
        todo_id = f"SI-{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC).isoformat()
        todo = {
            "todo_id": todo_id,
            "title": title,
            "description": description,
            "status": "pending",
            "created_at": now,
        }
        self._todos[todo_id] = todo
        return todo

    def validate_improvement(self, worktree_path: str) -> ValidationResult:
        runner = ValidationRunner(
            todo_id="self-improve",
            worktree_path=worktree_path,
            test_commands=["make test-unit"],
        )
        return runner.run_validation()

    def apply_improvement(
        self, todo_id: str, validation_result: ValidationResult
    ) -> ApplyResult:
        if not validation_result.success:
            return ApplyResult(
                todo_id=todo_id,
                applied=False,
                reload_needed=False,
                validation_passed=False,
            )

        entry = self._todos.get(todo_id)
        if entry is not None:
            entry["status"] = "applied"

        return ApplyResult(
            todo_id=todo_id,
            applied=True,
            reload_needed=True,
            validation_passed=True,
        )

    def reload_if_needed(self, apply_result: ApplyResult) -> ReloadResult:
        if not apply_result.reload_needed:
            return ReloadResult(
                reload_id="no-reload",
                reload_type=ReloadType.CONFIG,
                status="pending",
                message="Reload not needed — validation did not pass",
            )
        rr = self._reload_manager.request_reload(ReloadType.WORKER_CODE)
        return self._reload_manager.execute_reload(rr.reload_id)
