"""Decision applier that applies a TaskDecision to the todo repository."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agentic_harness.db.repository import TodoRepository
from agentic_harness.schemas.task_decision import TaskDecision
from agentic_harness.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)

_DECISION_STATUS_MAP: dict[str, TodoStatus] = {
    "complete": TodoStatus.COMPLETE,
    "needs_more_work": TodoStatus.NEEDS_MORE_WORK,
    "failed": TodoStatus.FAILED,
    "blocked": TodoStatus.BLOCKED,
    "manual_hold": TodoStatus.MANUAL_HOLD,
}

_LOW_CONFIDENCE_THRESHOLD = 0.5


async def apply_decision(
    decision: TaskDecision,
    todo_repo: TodoRepository,
    session: AsyncSession,
) -> None:
    if decision.decision == "complete" and not decision.evidence_refs:
        raise ValueError(
            "Cannot mark complete without evidence_refs. "
            "Provide at least one evidence artifact reference."
        )

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

    await todo_repo.transition(decision.matched_todo_id, target_status, todo.version)

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
