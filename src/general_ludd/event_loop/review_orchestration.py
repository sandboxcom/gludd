"""Non-blocking in-process review orchestration for the central event loop."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from general_ludd.event_loop.review_compaction import update_compaction_accuracy
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn
from general_ludd.self_improve.promotion import ManagedPromotionReceipt
from general_ludd.self_improve.staging import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
)

logger = logging.getLogger(__name__)


def is_managed_self_improve_todo(todo: object) -> bool:
    """Return whether a todo carries the explicit managed approval contract."""
    return (
        getattr(todo, "work_type", None) == "self_improve"
        and getattr(todo, "approval_policy", None)
        == MANAGED_SELF_IMPROVE_APPROVAL_POLICY
    )


def safe_string_attribute(
    obj: Any,
    attr: str,
    default: str | None = None,
) -> str | None:
    """Return a string attribute without trusting arbitrary record values."""
    value = getattr(obj, attr, default)
    return value if isinstance(value, str) else default


def _selected_reviewer(loop: Any) -> Any:
    review_config = loop.config.get("review", {}) if isinstance(loop.config, dict) else {}
    consensus_config = (
        loop.config.get("consensus_review", {})
        if isinstance(loop.config, dict)
        else {}
    )
    if review_config.get("use_langgraph") and loop._langgraph_reviewer is not None:
        return loop._langgraph_reviewer
    if consensus_config.get("enabled", False) and loop._consensus_reviewer is not None:
        return loop._consensus_reviewer
    assert loop._reviewer is not None
    return loop._reviewer


def _task_return_from_record(record: Any) -> TaskReturn:
    return_id = getattr(record, "return_id", "")
    return TaskReturn(
        return_id=return_id,
        todo_id=getattr(record, "todo_id", None),
        job_id=getattr(record, "job_id", None) or f"JOB-{return_id}",
        playbook=getattr(record, "playbook", None) or "noop.yml",
        queue=safe_string_attribute(record, "queue", "model") or "model",
        work_type=safe_string_attribute(record, "work_type", "review") or "review",
        exit_code=int(getattr(record, "exit_code", 0) or 0),
        result_summary=safe_string_attribute(record, "result_summary", "") or "",
    )


async def _review_or_manual_hold(
    loop: Any,
    reviewer: Any,
    task_return: TaskReturn,
) -> TaskDecision:
    try:
        return cast(
            TaskDecision,
            await loop._bounded_to_thread(
                reviewer.review_return,
                task_return,
                candidate_todos=[],
                artifacts=[],
            ),
        )
    except Exception as exc:
        logger.error("Reviewer raised for return %s: %s", task_return.return_id, exc)
        return TaskDecision(
            return_id=task_return.return_id,
            matched_todo_id=task_return.todo_id,
            decision="manual_hold",
            confidence=0.0,
            audit_notes=[f"Reviewer error: {exc}"],
        )


async def _managed_promotion(
    loop: Any,
    record: Any,
    task_return: TaskReturn,
    decision: TaskDecision,
) -> tuple[ManagedPromotionReceipt | None, bool]:
    if decision.decision != "complete" or task_return.work_type != "self_improve":
        return None, True
    project_id = getattr(record, "project_id", None)
    if not isinstance(project_id, str):
        project_id = None
    todo = await loop._todo_repo.get_by_id(
        task_return.todo_id,
        project_id=project_id,
    )
    if todo is None:
        logger.error(
            "Managed promotion todo %s no longer exists",
            task_return.todo_id,
        )
        await loop._release_managed_review_for_retry(record)
        return None, False
    if not is_managed_self_improve_todo(todo):
        return None, True
    try:
        await loop._persist_in_process_decision(record, decision)
    except Exception as exc:
        logger.error(
            "Decision persistence failed for return %s: %s",
            task_return.return_id,
            exc,
        )
        await loop._release_managed_review_for_retry(record)
        return None, False
    try:
        receipt = await loop._ensure_managed_self_improve_promotion(record, todo)
    except Exception as exc:
        logger.error(
            "Managed promotion failed for return %s: %s",
            task_return.return_id,
            exc,
        )
        await loop._release_managed_review_for_retry(record)
        return None, False
    return receipt, True


async def _apply_review_decision(
    loop: Any,
    record: Any,
    task_return: TaskReturn,
    decision: TaskDecision,
    promotion_receipt: ManagedPromotionReceipt | None,
) -> bool:
    from general_ludd.review.decision_applier import apply_decision

    try:
        project_id = getattr(record, "project_id", None) or None
        await apply_decision(
            decision,
            loop._todo_repo,
            loop._active_session,
            repo_root=loop._resolve_repo_root(project_id),
            managed_promotion_receipt=promotion_receipt,
        )
        await loop._active_session.flush()
        return True
    except Exception as exc:
        logger.error(
            "apply_decision failed for return %s (decision=%s): %s",
            task_return.return_id,
            getattr(decision, "decision", "?"),
            exc,
        )
        return False


async def _write_review_audit(
    loop: Any,
    record: Any,
    decision: TaskDecision,
    return_id: str,
) -> None:
    if loop._audit_repo is None:
        return
    try:
        await loop._audit_repo.create(
            event_type="return_reviewed",
            entity_type="task_return",
            entity_id=return_id,
            project_id=getattr(record, "project_id", None),
            details=json.dumps(
                {
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "matched_todo_id": decision.matched_todo_id,
                }
            ),
        )
    except Exception:
        logger.warning(
            "Audit write failed for return_reviewed event %s",
            return_id,
            exc_info=True,
        )


class EventLoopReviewMixin:
    """Provide review orchestration without expanding the scheduling core."""

    async def _review_in_process(self, record: Any) -> None:
        """Review one return off-loop and apply its bounded decision."""
        loop: Any = self
        reviewer = _selected_reviewer(loop)
        task_return = _task_return_from_record(record)
        decision = await _review_or_manual_hold(loop, reviewer, task_return)
        assert loop._todo_repo is not None
        assert loop._active_session is not None
        promotion_receipt, proceed = await _managed_promotion(
            loop,
            record,
            task_return,
            decision,
        )
        if not proceed:
            return
        if not await _apply_review_decision(
            loop,
            record,
            task_return,
            decision,
            promotion_receipt,
        ):
            return
        await _write_review_audit(loop, record, decision, task_return.return_id)
        logger.info(
            "In-process review for return %s -> %s",
            task_return.return_id,
            decision.decision,
        )
        update_compaction_accuracy(loop, decision)


__all__ = [
    "EventLoopReviewMixin",
    "is_managed_self_improve_todo",
    "safe_string_attribute",
]
