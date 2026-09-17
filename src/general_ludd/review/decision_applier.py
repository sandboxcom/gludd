"""Decision applier that applies a TaskDecision to the todo repository."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus

if TYPE_CHECKING:
    from general_ludd.self_improve.promotion import ManagedPromotionReceipt

logger = logging.getLogger(__name__)


def _gate_failure_summary(report: dict[str, object]) -> str:
    """Render the failing per-check summaries from a run_project_gate report."""
    checks = report.get("checks")
    lines: list[str] = []
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict) and not check.get("passed", True):
                summary = check.get("summary")
                if isinstance(summary, str) and summary:
                    lines.append(summary)
                else:
                    lines.append(f"{check.get('name', 'check')}: FAIL")
    if not lines:
        lines.append("project gate FAILED")
    return "; ".join(lines)

_DECISION_STATUS_MAP: dict[str, TodoStatus] = {
    "complete": TodoStatus.COMPLETE,
    "needs_more_work": TodoStatus.NEEDS_MORE_WORK,
    "failed": TodoStatus.FAILED,
    "blocked": TodoStatus.BLOCKED,
    "manual_hold": TodoStatus.MANUAL_HOLD,
}

_LOW_CONFIDENCE_THRESHOLD = 0.5


def _is_managed_self_improve(todo: object) -> bool:
    """Return whether a todo carries the explicit managed approval contract."""
    from general_ludd.self_improve.staging import (
        MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
    )

    return (
        getattr(todo, "work_type", None) == "self_improve"
        and getattr(todo, "approval_policy", None)
        == MANAGED_SELF_IMPROVE_APPROVAL_POLICY
    )


async def apply_decision(
    decision: TaskDecision,
    todo_repo: TodoRepository,
    session: AsyncSession,
    *,
    repo_root: str | None = None,
    managed_promotion_receipt: ManagedPromotionReceipt | None = None,
) -> None:
    """Validate and apply one review decision to its bound todo."""
    if decision.decision == "complete":
        from general_ludd.review.completion_verifier import verify_completion
        decision = await asyncio.to_thread(verify_completion, decision, None, repo_root)

    # D2: gate a still-COMPLETE decision behind the EXTERNAL target project's
    # own quality gate when that project declares one via ``project.yml``. This
    # is the sole production caller of run_project_gate — an external project's
    # lint/typecheck/test failures must block the merge decision. Fail-safe: any
    # error resolving/running the gate leaves the decision untouched (the gate is
    # opt-in via project.yml and must never crash the reconcile path).
    gate_report: dict[str, object] | None = None
    if decision.decision == "complete" and repo_root is not None:
        workspace = Path(repo_root)
        if (workspace / "project.yml").is_file():
            from general_ludd.quality.project_gate import run_project_gate

            try:
                gate_report = await asyncio.to_thread(
                    run_project_gate, str(workspace)
                )
            except Exception as exc:
                logger.warning(
                    "Project gate errored for return %s (repo_root=%s): %s — "
                    "leaving decision unchanged (fail-safe)",
                    decision.return_id,
                    repo_root,
                    exc,
                )
                gate_report = None
            else:
                if isinstance(gate_report, dict) and not gate_report.get("passed"):
                    summary = _gate_failure_summary(gate_report)
                    logger.warning(
                        "Project gate FAILED for return %s — downgrading "
                        "complete -> needs_more_work: %s",
                        decision.return_id,
                        summary,
                    )
                    decision = decision.model_copy(update={
                        "decision": "needs_more_work",
                        "confidence": 0.0,
                        "audit_notes": [
                            *decision.audit_notes,
                            f"Downgraded by project gate: {summary}",
                        ],
                        "child_todos": [
                            *decision.child_todos,
                            {
                                "title": (
                                    f"Fix project gate failures "
                                    f"(return {decision.return_id})"
                                ),
                                "description": (
                                    f"Project gate FAILED: {summary}"
                                ),
                            },
                        ],
                    })

    if decision.decision == "ignore_duplicate":
        logger.info("Ignoring duplicate return %s", decision.return_id)
        return

    if decision.matched_todo_id is None:
        logger.warning(
            "Decision for return %s has no matched_todo_id, nothing to apply",
            decision.return_id,
        )
        return

    todo = await todo_repo.get_by_id(decision.matched_todo_id)
    if todo is None:
        logger.error("Matched todo %s not found", decision.matched_todo_id)
        return

    target_status = _DECISION_STATUS_MAP.get(decision.decision)
    if target_status is None:
        logger.warning("Unknown decision type: %s", decision.decision)
        return

    if (
        target_status is TodoStatus.COMPLETE
        and _is_managed_self_improve(todo)
    ):
        if managed_promotion_receipt is None:
            raise ValueError(
                "managed self-improvement COMPLETE requires a promotion receipt"
            )
        project_id = getattr(todo, "project_id", None)
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("managed self-improvement todo requires a project identity")
        managed_promotion_receipt.verify_for(
            todo_id=decision.matched_todo_id,
            project_id=project_id,
            repo_root=repo_root,
            return_id=decision.return_id,
        )

    await todo_repo.transition(
        decision.matched_todo_id,
        target_status,
        todo.version,
        project_id=todo.project_id,
    )

    if decision.child_todos:
        for child_data in decision.child_todos:
            child_payload: dict[str, Any] = {
                "title": child_data.get("title", "Child task"),
                "description": child_data.get("description", ""),
                "parent_todo_id": decision.matched_todo_id,
                "status": TodoStatus.BACKLOG,
                "work_type": "code",
            }
            await todo_repo.create(child_payload)

    if decision.confidence < _LOW_CONFIDENCE_THRESHOLD:
        validation_payload: dict[str, Any] = {
            "title": f"Validate return {decision.return_id}",
            "description": (
                f"Low confidence ({decision.confidence}) decision on return "
                f"{decision.return_id}. Manual validation recommended."
            ),
            "parent_todo_id": decision.matched_todo_id,
            "status": TodoStatus.BACKLOG,
            "work_type": "review",
        }
        await todo_repo.create(validation_payload)

    logger.info(
        "Applied decision %s to todo %s", decision.decision, decision.matched_todo_id
    )
