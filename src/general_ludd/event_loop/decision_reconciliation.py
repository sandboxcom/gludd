"""Apply reviewed task decisions without coupling policy to the tick driver."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select

from general_ludd.db.models import AuditEventType, TaskDecisionModel
from general_ludd.db.repository import ConcurrencyError
from general_ludd.event_loop.review_orchestration import is_managed_self_improve_todo
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)

_VALIDATION_COMMAND = re.compile(r"make [A-Za-z0-9_.-]{1,128}\Z")
_MAX_VALIDATION_COMMANDS = 16
_DEFAULT_VALIDATION_TIMEOUT_SECONDS = 300.0
_MAX_VALIDATION_TIMEOUT_SECONDS = 600.0


async def reconcile_completed_decisions(loop: Any) -> None:
    """Reconcile the newest persisted review decisions for one event-loop tick."""
    if loop._active_session is None or loop._todo_repo is None:
        return
    project_id = loop._tick_project_id
    stmt = select(TaskDecisionModel).order_by(TaskDecisionModel.created_at.desc()).limit(50)
    if project_id is not None:
        stmt = stmt.where(TaskDecisionModel.project_id == project_id)
    result = await loop._active_session.execute(stmt)
    decisions = list(result.scalars().all())
    todo_map = await _load_todos(loop, decisions, project_id)
    reconciled = 0
    push_failures = 0
    for decision in decisions:
        applied, push_failed = await _reconcile_one(loop, decision, todo_map)
        reconciled += int(applied)
        push_failures += int(push_failed)
    loop._tick_metrics["decisions_applied"] = reconciled
    loop._tick_metrics["push_failures"] = push_failures


async def _load_todos(
    loop: Any,
    decisions: list[Any],
    project_id: str | None,
) -> dict[str, Any]:
    todo_ids = [decision.matched_todo_id for decision in decisions if decision.matched_todo_id]
    if not todo_ids or loop._todo_repo is None:
        return {}
    fetched_todos = await loop._todo_repo.get_by_ids(todo_ids, project_id=project_id)
    if isinstance(fetched_todos, Mapping):
        return dict(fetched_todos)
    if not hasattr(loop._todo_repo, "get_by_id"):
        return {}
    todo_map: dict[str, Any] = {}
    for todo_id in todo_ids:
        todo = await loop._todo_repo.get_by_id(todo_id, project_id=project_id)
        if todo is not None:
            todo_map[todo_id] = todo
    return todo_map


async def _reconcile_one(
    loop: Any,
    decision: Any,
    todo_map: dict[str, Any],
) -> tuple[bool, bool]:
    todo_id = decision.matched_todo_id
    if not todo_id:
        return False, False
    decision_id = loop._decision_id(decision)
    validation_key = _validation_decision_key(decision_id)
    if validation_key in loop._applied_decisions:
        return False, False
    if decision_id in loop._applied_decisions:
        return False, await _retry_unpushed_completion(loop, decision, todo_map)
    todo = todo_map.get(todo_id)
    if todo is None or todo.status != TodoStatus.REVIEWING_RETURN.value:
        return False, False
    new_status = loop._decision_to_status(decision.decision)
    if new_status is None:
        return False, False
    repo_root: str | None = None
    if decision.decision == "complete":
        verified = await _verify_completion_evidence(loop, decision, todo, decision_id)
        if verified is None:
            return False, False
        new_status, repo_root = verified
        new_status = await _apply_project_gate(loop, decision_id, repo_root, new_status)
        new_status = await _apply_human_gate(loop, decision, todo, decision_id, new_status)
        validation_status = await _apply_requested_validation(
            loop,
            decision,
            todo,
            decision_id,
            repo_root,
            new_status,
        )
        if validation_status is None:
            return True, False
        new_status = validation_status
    if new_status == TodoStatus.COMPLETE and is_managed_self_improve_todo(todo):
        promoted = await _promote_managed_todo(loop, decision, todo, repo_root)
        if not promoted:
            return False, False
    return await _commit_transition(loop, decision, todo, decision_id, new_status)


def _validation_decision_key(decision_id: str) -> str:
    """Keep validation dispatch idempotency distinct from completed delivery."""
    return f"validation:{decision_id}"


def _validation_config(loop: Any) -> Mapping[str, object] | None:
    """Return the explicitly enabled configuration; absence stays default-off."""
    raw_config = getattr(loop, "config", None)
    config = raw_config if isinstance(raw_config, Mapping) else {}
    candidate = config.get("validation_requests")
    if not isinstance(candidate, Mapping) or candidate.get("enabled") is not True:
        return None
    return candidate


def _parse_validation_requests(decision: Any) -> list[str] | None:
    """Decode one bounded request without interpreting its prose as instructions."""
    try:
        requests = json.loads(getattr(decision, "validation_requests", None) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(requests, list)
        or len(requests) > 1
        or any(not isinstance(request, str) or not request.strip() for request in requests)
    ):
        return None
    return requests


def _validation_commands(config: Mapping[str, object]) -> tuple[str, ...] | None:
    """Admit only the make-only command grammar enforced by the playbook."""
    commands = config.get("commands")
    if (
        not isinstance(commands, list)
        or not 1 <= len(commands) <= _MAX_VALIDATION_COMMANDS
        or any(
            not isinstance(command, str)
            or _VALIDATION_COMMAND.fullmatch(command) is None
            for command in commands
        )
    ):
        return None
    return tuple(commands)


def _validation_timeout(config: Mapping[str, object]) -> float | None:
    """Return one finite worker timeout constrained to its deployed ceiling."""
    raw_timeout = config.get(
        "timeout_seconds",
        _DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    )
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
        return None
    timeout = float(raw_timeout)
    if not math.isfinite(timeout) or not 0.0 < timeout <= _MAX_VALIDATION_TIMEOUT_SECONDS:
        return None
    return timeout


def _validation_worktree(repo_root: str | None) -> str | None:
    """Resolve the trusted project binding; reviewer text never supplies a path."""
    if repo_root is None:
        return None
    try:
        worktree = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return str(worktree) if worktree.is_dir() else None


async def _apply_requested_validation(
    loop: Any,
    decision: Any,
    todo: Any,
    decision_id: str,
    repo_root: str | None,
    status: TodoStatus,
) -> TodoStatus | None:
    """Dispatch one trusted validation job or return a fail-closed status.

    ``None`` means that a TaskReturn was durably persisted and the todo is back
    in its ordinary result-review lifecycle. No completion transition is then
    allowed for the requesting decision.
    """
    if status is not TodoStatus.COMPLETE:
        return status
    config = _validation_config(loop)
    if config is None:
        return status
    requests = _parse_validation_requests(decision)
    if requests is None:
        return TodoStatus.BLOCKED
    if not requests:
        return status
    worktree_path = _validation_worktree(repo_root)
    commands = _validation_commands(config)
    if worktree_path is None or commands is None:
        return TodoStatus.NEEDS_MORE_WORK
    timeout_seconds = _validation_timeout(config)
    if timeout_seconds is None:
        return TodoStatus.BLOCKED
    try:
        persisted = await loop._dispatch_validate_job(
            todo,
            decision_id=decision_id,
            worktree_path=worktree_path,
            test_commands=commands,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        logger.warning(
            "Validation dispatch failed for todo %s",
            todo.todo_id,
            exc_info=True,
        )
        return TodoStatus.BLOCKED
    if not persisted:
        return TodoStatus.BLOCKED
    loop._ledger_add(loop._applied_decisions, _validation_decision_key(decision_id))
    return None


async def _retry_unpushed_completion(
    loop: Any,
    decision: Any,
    todo_map: dict[str, Any],
) -> bool:
    if decision.decision != "complete" or decision.matched_todo_id in loop._pushed_work:
        return False
    todo = todo_map.get(decision.matched_todo_id)
    return todo is not None and await loop._attempt_completed_push(todo)


async def _verify_completion_evidence(
    loop: Any,
    decision: Any,
    todo: Any,
    decision_id: str,
) -> tuple[TodoStatus, str | None] | None:
    try:
        evidence_refs: list[str] = json.loads(getattr(decision, "evidence_refs", None) or "[]")
        audit_notes: list[str] = json.loads(getattr(decision, "audit_notes", None) or "[]")
        schema_decision = TaskDecision(
            return_id=getattr(decision, "return_id", "") or "",
            matched_todo_id=decision.matched_todo_id,
            decision=decision.decision,
            confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            evidence_refs=evidence_refs,
            audit_notes=audit_notes,
        )
    except (ValueError, TypeError) as exc:
        logger.warning(
            "Reconcile: skipping decision %s for todo %s — could not parse/build evidence for gating: %s",
            decision_id,
            decision.matched_todo_id,
            exc,
        )
        return None
    project_id = getattr(decision, "project_id", None) or getattr(todo, "project_id", None) or None
    repo_root = loop._resolve_repo_root(project_id)
    from general_ludd.review.completion_verifier import verify_completion

    verified = await loop._bounded_to_thread(verify_completion, schema_decision, None, repo_root)
    status = loop._decision_to_status(verified.decision)
    if verified.decision != "complete":
        logger.warning(
            "Reconcile: evidence gate downgraded %s from complete to %s for todo %s",
            decision_id,
            verified.decision,
            decision.matched_todo_id,
        )
        if status is None:
            return None
    return status or TodoStatus.COMPLETE, repo_root


async def _apply_project_gate(
    loop: Any,
    decision_id: str,
    repo_root: str | None,
    status: TodoStatus,
) -> TodoStatus:
    if repo_root is None:
        return status
    workspace = Path(repo_root)
    if not (workspace / "project.yml").is_file():
        return status
    from general_ludd.quality.project_gate import run_project_gate

    try:
        report = await loop._bounded_to_thread(run_project_gate, str(workspace))
    except Exception as exc:
        logger.warning(
            "Reconcile: project gate errored for decision %s "
            "(repo_root=%s): %s — leaving decision unchanged (fail-safe)",
            decision_id,
            repo_root,
            exc,
        )
        return status
    if not isinstance(report, dict) or report.get("passed"):
        return status
    checks = report.get("checks")
    summaries = (
        [
            str(check.get("summary") or f"{check.get('name', 'check')}: FAIL")
            for check in checks
            if isinstance(check, dict) and not check.get("passed", True)
        ]
        if isinstance(checks, list)
        else []
    )
    summary = "; ".join(summaries) if summaries else "project gate FAILED"
    logger.warning(
        "Reconcile: project gate FAILED for decision %s — downgrading complete -> needs_more_work: %s",
        decision_id,
        summary,
    )
    return TodoStatus.NEEDS_MORE_WORK


async def _apply_human_gate(
    loop: Any,
    decision: Any,
    todo: Any,
    decision_id: str,
    status: TodoStatus,
) -> TodoStatus:
    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    if decision.decision != "complete" or not loop._human_gate.should_interrupt(confidence):
        return status
    gate_decision = await loop._human_gate.await_approval(
        thread_id=decision_id,
        message=f"Review decision {decision_id} for todo {todo.todo_id}",
        decision_id=decision_id,
        todo_id=todo.todo_id,
        confidence=confidence,
    )
    if gate_decision is not None and gate_decision.lower() in ("denied", "needs_more_work"):
        return TodoStatus.NEEDS_MORE_WORK
    return status


async def _promote_managed_todo(
    loop: Any,
    decision: Any,
    todo: Any,
    repo_root: str | None,
) -> bool:
    if loop._task_return_repo is None:
        logger.error("Reconcile: managed todo %s has no task-return repository", todo.todo_id)
        return False
    task_return_row = await loop._task_return_repo.get_by_id(decision.return_id)
    if task_return_row is None:
        logger.error(
            "Reconcile: managed todo %s is missing return %s",
            todo.todo_id,
            decision.return_id,
        )
        return False
    try:
        receipt = await loop._ensure_managed_self_improve_promotion(task_return_row, todo)
        receipt.verify_for(
            todo_id=todo.todo_id,
            project_id=todo.project_id,
            repo_root=repo_root,
            return_id=decision.return_id,
        )
    except Exception as exc:
        logger.error(
            "Reconcile: managed promotion blocked COMPLETE for todo %s: %s",
            todo.todo_id,
            exc,
        )
        return False
    return True


async def _commit_transition(
    loop: Any,
    decision: Any,
    todo: Any,
    decision_id: str,
    new_status: TodoStatus,
) -> tuple[bool, bool]:
    try:
        await loop._todo_repo.transition(todo.todo_id, new_status, todo.version)
    except ConcurrencyError as exc:
        logger.info(
            "Reconcile lost version race for todo %s (decision %s): %s — skipping stale reconcile",
            todo.todo_id,
            decision_id,
            exc,
        )
        return False, False
    loop._ledger_add(loop._applied_decisions, decision_id)
    loop._track_background_task(asyncio.create_task(loop._auto_record_episode(todo, new_status, decision)))
    push_failed = (
        new_status == TodoStatus.COMPLETE
        and decision.decision == "complete"
        and await loop._attempt_completed_push(todo)
    )
    if new_status == TodoStatus.COMPLETE and loop._ephemeral_account_manager is not None:
        loop._track_background_task(asyncio.create_task(loop._maybe_cleanup_ephemeral(todo)))
    await _record_audit_event(loop, decision, todo, new_status)
    return True, push_failed


async def _record_audit_event(
    loop: Any,
    decision: Any,
    todo: Any,
    new_status: TodoStatus,
) -> None:
    if loop._audit_repo is None:
        return
    try:
        await loop._audit_repo.record_typed(
            AuditEventType.TODO_STATUS_CHANGED,
            entity_type="todo",
            entity_id=todo.todo_id,
            project_id=todo.project_id,
            details={
                "old": todo.status,
                "new": new_status.value,
                "decision": decision.decision,
            },
        )
    except Exception:
        logger.warning(
            "Audit write failed for todo status change %s",
            todo.todo_id,
            exc_info=True,
        )


__all__ = ("reconcile_completed_decisions",)
