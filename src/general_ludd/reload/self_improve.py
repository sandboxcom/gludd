"""Self-improvement workflow — validate, apply, and reload harness improvements."""

from __future__ import annotations

import logging
import shlex
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from general_ludd.project_runner.profile import load_project_profile
from general_ludd.reload.hot_reloader import HotReloader
from general_ludd.reload.manager import ReloadManager, ReloadResult, ReloadType
from general_ludd.validation.runner import ValidationResult, ValidationRunner

logger = logging.getLogger(__name__)

_HARDCODED_FALLBACK = ["make test-unit"]


def _resolve_test_commands(worktree_path: str) -> list[str]:
    """Detect the target project's test command from its toolchain profile.

    Uses :func:`load_project_profile` (explicit ``project.yml`` → marker-file
    detection) to discover the test command the project actually uses. Falls
    back to ``["make test-unit"]`` when detection fails (no profile, no
    recognized markers, or the profile has no ``test`` command), so a
    legacy/undetectable project can still be validated.
    """
    try:
        profile = load_project_profile(worktree_path)
    except Exception:
        logger.debug("no project profile for %s — using fallback test command", worktree_path)
        return _HARDCODED_FALLBACK
    if not profile.has("test"):
        logger.debug("project profile for %s has no 'test' command — using fallback", worktree_path)
        return _HARDCODED_FALLBACK
    try:
        argv = profile.resolve_argv("test")
    except Exception:
        logger.warning("could not resolve 'test' command for %s — using fallback", worktree_path)
        return _HARDCODED_FALLBACK
    test_cmd = shlex.join(argv)
    logger.debug("detected test command for %s: %s", worktree_path, test_cmd)
    return [test_cmd]


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
        self._todos: dict[str, dict[str, object]] = {}
        # Optional concrete hot-rotation target. When set, reload_if_needed
        # performs a REAL os.replace + importlib.reload + health gate via the
        # HotReloader instead of the in-memory manager bookkeeping.
        self._code_target: tuple[str, str] | None = None
        self._health_check: Callable[[], bool] | None = None
        # The base snapshot the candidate was generated against — enables the
        # anti-clobber 3-way merge in reload_code_module so a concurrent edit to
        # the live file is never silently reverted.
        self._base_source_path: str | None = None
        # The sha256 of the exact candidate bytes this workflow produced and
        # validated. When set, reload_code_module verifies the candidate on disk
        # hashes to this value BEFORE the swap+reload (fail-closed authenticity
        # gate, task #20); a tampered/unexpected candidate is refused. None keeps
        # the legacy verbatim-swap behavior.
        self._expected_sha256: str | None = None

    def set_code_target(
        self,
        module_name: str,
        candidate_source_path: str,
        health_check: Callable[[], bool] | None = None,
        base_source_path: str | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        """Arm a real leaf-module hot-rotation for the next reload_if_needed.

        ``base_source_path`` is the pre-generation snapshot of the module; when
        supplied it activates the anti-clobber 3-way merge so an OVERLAPPING
        concurrent edit refuses the reload (fail-closed) instead of being
        clobbered. Without it the candidate is swapped verbatim (unchanged API).

        ``expected_sha256`` is the digest of the exact candidate bytes this
        workflow produced/validated; when supplied, reload_code_module verifies
        the candidate on disk matches it BEFORE any swap or importlib.reload and
        REFUSES (fail-closed) on mismatch, so a tampered or wrong candidate is
        never loaded and executed. Without it the candidate is not hash-checked
        (unchanged API).
        """
        self._code_target = (module_name, candidate_source_path)
        self._health_check = health_check
        self._base_source_path = base_source_path
        self._expected_sha256 = expected_sha256

    def create_improvement_todo(self, title: str, description: str) -> dict[str, object]:
        todo_id = f"SI-{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC).isoformat()
        todo: dict[str, object] = {
            "todo_id": todo_id,
            "title": title,
            "description": description,
            "status": "pending",
            "created_at": now,
        }
        self._todos[todo_id] = todo
        return todo

    def validate_improvement(self, worktree_path: str) -> ValidationResult:
        # D7 hardening: worktree_path reaches here from the (attacker-facing)
        # self-improve HTTP payload unconfined. ValidationRunner does not know
        # the caller's worktree layout, so this call site derives the
        # confinement root itself — the path's own parent directory — and
        # requires the (symlink-resolved) worktree_path to remain a
        # descendant of it. This does not defend a fully attacker-chosen
        # standalone path, but it DOES fail closed on the concrete attack this
        # guard exists for: a worktree entry that is (or resolves through) a
        # symlink escaping to somewhere outside its own parent, e.g. planted
        # by a prior compromised run. Trailing slashes are stripped first so
        # ``dirname`` doesn't degenerate to the path itself.
        import os

        expected_root = os.path.dirname(worktree_path.rstrip("/")) or "/"
        try:
            test_commands = _resolve_test_commands(worktree_path)
            runner = ValidationRunner(
                todo_id="self-improve",
                worktree_path=worktree_path,
                test_commands=test_commands,
                expected_worktree_root=expected_root,
            )
            return runner.run_validation()
        except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
            # A missing/unusable/out-of-root worktree is a validation
            # failure, not a crash — fail closed so the improvement is not
            # applied. ValueError covers CommandValidationError raised by
            # ValidationRunner's confinement guard (a ValueError subclass).
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
            try:
                verdict = self._hot_reloader.reload_code_module(
                    module_name=module_name,
                    candidate_source_path=candidate_path,
                    health_check=self._health_check,
                    base_source_path=self._base_source_path,
                    expected_sha256=self._expected_sha256,
                )
            except Exception as exc:
                # reload_code_module rolls back on all of its KNOWN failure
                # branches (import error, health fail, merge conflict). This is
                # the belt-and-suspenders guard so an UNEXPECTED error can never
                # propagate into the event-loop tick — report failed and leave
                # the live module as reload_code_module last set it.
                return ReloadResult(
                    reload_id=uuid.uuid4().hex[:12],
                    reload_type=ReloadType.WORKER_CODE,
                    status="failed",
                    message=f"reload raised unexpectedly: {exc}",
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
