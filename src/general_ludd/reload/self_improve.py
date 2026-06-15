"""Self-improvement workflow — validate, apply, and reload harness improvements."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from general_ludd.reload.hot_reloader import HotReloader
from general_ludd.reload.manager import ReloadManager, ReloadResult, ReloadType
from general_ludd.validation.runner import ValidationResult, ValidationRunner


@dataclass
class ApplyResult:
    todo_id: str
    applied: bool
    reload_needed: bool
    validation_passed: bool


class SelfImprovementWorkflow:
    def __init__(self, config_dir: str = "config") -> None:
        self._reload_manager = ReloadManager()
        self._hot_reloader = HotReloader(config_dir=config_dir)
        self._todos: dict[str, dict[str, Any]] = {}
        # Optional concrete hot-rotation target. When set, reload_if_needed
        # performs a REAL os.replace + importlib.reload + health gate via the
        # HotReloader instead of the in-memory manager bookkeeping.
        self._code_target: tuple[str, str] | None = None
        self._health_check: Callable[[], bool] | None = None

    def set_code_target(
        self,
        module_name: str,
        candidate_source_path: str,
        health_check: Callable[[], bool] | None = None,
    ) -> None:
        """Arm a real leaf-module hot-rotation for the next reload_if_needed."""
        self._code_target = (module_name, candidate_source_path)
        self._health_check = health_check

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
        try:
            return runner.run_validation()
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            # A missing/unusable worktree is a validation failure, not a crash —
            # fail closed so the improvement is not applied.
            return ValidationResult(
                success=False,
                passed_count=0,
                failed_count=1,
                output=f"validation could not run: {exc}",
                failures=[str(exc)],
            )

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

        # Real hot-rotation when a concrete leaf module + candidate are armed:
        # os.replace the candidate over the live file, importlib.reload, then a
        # health gate with auto-rollback. The verdict is mapped onto the
        # manager-style ReloadResult the callers expect.
        if self._code_target is not None:
            module_name, candidate_path = self._code_target
            verdict = self._hot_reloader.reload_code_module(
                module_name=module_name,
                candidate_source_path=candidate_path,
                health_check=self._health_check,
            )
            status = "success" if verdict.success else "failed"
            message = verdict.error or f"Hot-rotated {module_name}"
            if not verdict.success and verdict.details.get("rolled_back"):
                message = f"Reload rolled back: {verdict.error or 'health gate failed'}"
            return ReloadResult(
                reload_id=uuid.uuid4().hex[:12],
                reload_type=ReloadType.WORKER_CODE,
                status=status,
                message=message,
            )

        rr = self._reload_manager.request_reload(ReloadType.WORKER_CODE)
        return self._reload_manager.execute_reload(rr.reload_id)
