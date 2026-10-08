"""Non-blocking in-process review orchestration for the central event loop."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from general_ludd.decision_codification.coordination import (
    run_with_decision_capture_lease,
)
from general_ludd.decision_codification.schema import (
    DecisionKind,
    FallbackReason,
    NormalizationRefusalReason,
    RolloutStage,
    VerifiedOutcome,
)
from general_ludd.decision_codification.service import (
    DecisionCodificationAdapter,
    DecisionResolution,
    DecisionResolutionSource,
)
from general_ludd.event_loop.review_compaction import update_compaction_accuracy
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn
from general_ludd.self_improve.promotion import ManagedPromotionReceipt
from general_ludd.self_improve.staging import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
)

logger = logging.getLogger(__name__)

_CODIFIED_REVIEW_ACTIONS = {
    "approve": "complete",
    "request_changes": "needs_more_work",
    "reject": "failed",
}
_TASK_DECISION_REVIEW_ACTIONS = {
    "complete": "approve",
    "needs_more_work": "request_changes",
    "failed": "reject",
    # These fallback-only decisions have no codified equivalent.  The resolver
    # still requires one closed REVIEW action, but the captured TaskDecision is
    # returned unchanged; use the conservative non-approval action here.
    "blocked": "reject",
    "manual_hold": "reject",
    "ignore_duplicate": "reject",
}
_STRING_CONTEXT_FEATURES = (
    "work_type",
    "queue",
    "risk_band",
    "resource_profile",
    "provider_class",
)
_BOOLEAN_CONTEXT_FEATURES = (
    "approval_required",
    "reversible",
    "required_evidence",
)
_INTEGER_CONTEXT_FEATURES = (
    "retry_count",
    "estimated_cost_microusd",
    "latency_ms",
)


@dataclass(frozen=True, slots=True)
class _ReviewDecisionAttribution:
    """Content-free provenance for one adapter-enabled live review."""

    source: DecisionResolutionSource
    candidate_digest: str | None = None
    decision_receipt_digest: str | None = None
    fallback_reason: FallbackReason | None = None
    normalization_reason: NormalizationRefusalReason | None = None
    project_id: str | None = None
    decision_kind: DecisionKind | None = None
    rollout_stage: RolloutStage | None = None
    application_id: str | None = None


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


def _review_context_features(record: Any, task_return: TaskReturn) -> object:
    """Project one return into the bounded live REVIEW feature vocabulary.

    Risk is deliberately never inferred.  A producer/recorder must supply an
    explicit low- or medium-risk band; missing, high-risk, or unsupported values
    reach the adapter as an ineligible context and therefore abstain.
    """
    if not isinstance(task_return.todo_id, str) or not task_return.todo_id:
        return None

    features: dict[str, object] = {
        "operation_class": "review",
        "fallback_allowed": True,
    }
    for name in _STRING_CONTEXT_FEATURES:
        value = safe_string_attribute(record, name)
        if value is not None:
            features[name] = value
    for name in _BOOLEAN_CONTEXT_FEATURES:
        value = getattr(record, name, None)
        if type(value) is bool:
            features[name] = value
    for name in _INTEGER_CONTEXT_FEATURES:
        value = getattr(record, name, None)
        if type(value) is int:
            features[name] = value
    exit_code = getattr(record, "exit_code", None)
    if type(exit_code) is int:
        features["status"] = "succeeded" if exit_code == 0 else "failed"
    return features


def _is_sha256_digest(value: object) -> bool:
    """Return whether a value is one exact lowercase SHA-256 identifier."""
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _attribution_from_resolution(
    resolution: DecisionResolution,
) -> _ReviewDecisionAttribution:
    """Reduce a resolution to closed reasons and digest-only attribution."""
    abstention = resolution.abstention
    return _ReviewDecisionAttribution(
        source=resolution.source,
        candidate_digest=resolution.candidate_digest,
        decision_receipt_digest=resolution.decision_receipt_digest,
        fallback_reason=abstention.reason if abstention is not None else None,
        normalization_reason=(
            abstention.normalization_reason if abstention is not None else None
        ),
        project_id=resolution.project_id,
        decision_kind=resolution.decision_kind,
        rollout_stage=resolution.rollout_stage,
        application_id=resolution.application_id,
    )


def _codified_task_decision(
    task_return: TaskReturn,
    resolution: DecisionResolution,
) -> TaskDecision:
    """Map only a fully attributed closed REVIEW hit into a TaskDecision."""
    mapped = _CODIFIED_REVIEW_ACTIONS.get(resolution.decision)
    if (
        resolution.source is not DecisionResolutionSource.CODIFIED
        or mapped is None
        or resolution.abstention is not None
        or not _is_sha256_digest(resolution.candidate_digest)
        or not _is_sha256_digest(resolution.decision_receipt_digest)
        or not isinstance(task_return.todo_id, str)
        or not task_return.todo_id
    ):
        raise ValueError("invalid codified REVIEW resolution")
    assert resolution.decision_receipt_digest is not None
    return TaskDecision(
        return_id=task_return.return_id,
        matched_todo_id=task_return.todo_id,
        decision=mapped,
        confidence=1.0,
        evidence_refs=[resolution.decision_receipt_digest],
    )


def _review_action_for_fallback(decision: TaskDecision) -> str:
    """Return a closed resolver action without narrowing fallback semantics."""
    return _TASK_DECISION_REVIEW_ACTIONS.get(decision.decision, "reject")


def _resolve_codified_or_review(
    adapter: DecisionCodificationAdapter,
    reviewer: Any,
    record: Any,
    task_return: TaskReturn,
) -> tuple[TaskDecision, _ReviewDecisionAttribution]:
    """Run exact local resolution and at most one reviewer call in one worker."""
    fallback_attempted = False
    fallback_decision: TaskDecision | None = None

    def fallback(_abstention: object) -> str:
        nonlocal fallback_attempted, fallback_decision
        fallback_attempted = True
        reviewed = reviewer.review_return(
            task_return,
            candidate_todos=[],
            artifacts=[],
        )
        if not isinstance(reviewed, TaskDecision):
            raise TypeError("reviewer returned an invalid TaskDecision")
        fallback_decision = reviewed
        return _review_action_for_fallback(reviewed)

    try:
        project_id = safe_string_attribute(record, "project_id", "") or ""
        resolution = adapter.resolve(
            project_id=project_id,
            decision_kind=DecisionKind.REVIEW,
            features=_review_context_features(record, task_return),
            correlation_id=f"return-review:{task_return.return_id}",
            now=datetime.now(UTC),
            side_effect_id=f"task-decision:{task_return.return_id}",
            fallback=fallback,
        )
        if not isinstance(resolution, DecisionResolution):
            raise TypeError("decision adapter returned an invalid resolution")
        attribution = _attribution_from_resolution(resolution)
        if resolution.source is DecisionResolutionSource.CODIFIED:
            return _codified_task_decision(task_return, resolution), attribution
        if (
            resolution.source is DecisionResolutionSource.AGENT_FALLBACK
            and resolution.abstention is not None
            and fallback_decision is not None
        ):
            return fallback_decision, attribution
        raise ValueError("decision adapter returned inconsistent fallback state")
    except Exception as exc:
        logger.warning(
            "Decision codification failed closed for return %s (%s)",
            task_return.return_id,
            type(exc).__name__,
        )
        runtime_attribution = _ReviewDecisionAttribution(
            source=DecisionResolutionSource.AGENT_FALLBACK,
            fallback_reason=FallbackReason.RUNTIME_ERROR,
        )
        if fallback_decision is not None:
            return fallback_decision, runtime_attribution
        if fallback_attempted:
            raise
        reviewed = reviewer.review_return(
            task_return,
            candidate_todos=[],
            artifacts=[],
        )
        if not isinstance(reviewed, TaskDecision):
            raise TypeError("reviewer returned an invalid TaskDecision") from exc
        return reviewed, runtime_attribution


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


async def _codified_review_or_manual_hold(
    loop: Any,
    adapter: DecisionCodificationAdapter,
    reviewer: Any,
    record: Any,
    task_return: TaskReturn,
) -> tuple[TaskDecision, _ReviewDecisionAttribution | None]:
    """Resolve an adapter-enabled review off-loop with legacy error handling."""
    try:
        result = await loop._bounded_to_thread(
            _resolve_codified_or_review,
            adapter,
            reviewer,
            record,
            task_return,
        )
        return cast(tuple[TaskDecision, _ReviewDecisionAttribution], result)
    except Exception as exc:
        logger.error("Reviewer raised for return %s: %s", task_return.return_id, exc)
        return (
            TaskDecision(
                return_id=task_return.return_id,
                matched_todo_id=task_return.todo_id,
                decision="manual_hold",
                confidence=0.0,
                audit_notes=[f"Reviewer error: {exc}"],
            ),
            None,
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
    attribution: _ReviewDecisionAttribution | None = None,
) -> None:
    if loop._audit_repo is None:
        return
    details: dict[str, object] = {
        "decision": decision.decision,
        "confidence": decision.confidence,
        "matched_todo_id": decision.matched_todo_id,
    }
    if attribution is not None:
        details["decision_source"] = attribution.source.value
        if attribution.candidate_digest is not None:
            details["candidate_digest"] = attribution.candidate_digest
        if attribution.decision_receipt_digest is not None:
            details["decision_receipt_digest"] = (
                attribution.decision_receipt_digest
            )
        if attribution.fallback_reason is not None:
            details["fallback_reason"] = attribution.fallback_reason.value
        if attribution.normalization_reason is not None:
            details["normalization_reason"] = attribution.normalization_reason.value
    try:
        await loop._audit_repo.create(
            event_type="return_reviewed",
            entity_type="task_return",
            entity_id=return_id,
            project_id=getattr(record, "project_id", None),
            details=json.dumps(details),
        )
    except Exception:
        logger.warning(
            "Audit write failed for return_reviewed event %s",
            return_id,
            exc_info=True,
        )


async def _record_codified_outcome(
    loop: Any,
    adapter: DecisionCodificationAdapter,
    attribution: _ReviewDecisionAttribution | None,
    *,
    outcome: VerifiedOutcome,
) -> None:
    """Persist content-free live feedback off-loop without changing task outcome."""
    if (
        attribution is None
        or attribution.source is not DecisionResolutionSource.CODIFIED
        or attribution.project_id is None
        or attribution.decision_kind is None
        or attribution.candidate_digest is None
        or attribution.rollout_stage is None
        or attribution.application_id is None
        or attribution.decision_receipt_digest is None
    ):
        return
    recorder = getattr(adapter, "record_application_outcome", None)
    if not callable(recorder):
        return
    try:
        await loop._bounded_to_thread(
            recorder,
            project_id=attribution.project_id,
            decision_kind=attribution.decision_kind,
            candidate_digest=attribution.candidate_digest,
            application_id=attribution.application_id,
            rollout_stage=attribution.rollout_stage,
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            terminal_event_id=attribution.application_id,
            evidence_digest=attribution.decision_receipt_digest,
        )
    except Exception as exc:
        logger.warning(
            "Decision outcome feedback failed closed for %s (%s)",
            attribution.application_id,
            type(exc).__name__,
        )


async def _record_agent_decision_outcome(
    loop: Any,
    adapter: DecisionCodificationAdapter,
    attribution: _ReviewDecisionAttribution | None,
    record: Any,
    task_return: TaskReturn,
    decision: TaskDecision,
    *,
    outcome: VerifiedOutcome,
) -> None:
    """Capture eligible fallback evidence off-loop without changing task outcome."""
    if (
        attribution is None
        or attribution.source is not DecisionResolutionSource.AGENT_FALLBACK
        or attribution.project_id is None
        or attribution.decision_kind is None
        or not isinstance(task_return.todo_id, str)
        or not task_return.todo_id
    ):
        return
    recorder = getattr(adapter, "record_agent_decision_outcome", None)
    if not callable(recorder):
        return
    try:
        capture_id = f"return-review:{task_return.return_id}"
        capture_arguments: dict[str, object] = {
            "project_id": attribution.project_id,
            "decision_kind": attribution.decision_kind,
            "features": _review_context_features(record, task_return),
            "decision": _review_action_for_fallback(decision),
            "capture_id": capture_id,
            "root_task_id": task_return.todo_id,
            "outcome": outcome,
            "occurred_at": datetime.now(UTC),
        }

        async def capture_operation() -> object:
            return await loop._bounded_to_thread(recorder, **capture_arguments)

        coordination_key = getattr(
            adapter,
            "agent_decision_coordination_key",
            None,
        )
        if not callable(coordination_key):
            await capture_operation()
            return
        lease_key = coordination_key(
            project_id=attribution.project_id,
            decision_kind=attribution.decision_kind,
            capture_id=capture_id,
            root_task_id=task_return.todo_id,
        )
        if lease_key is None:
            return
        await run_with_decision_capture_lease(
            loop._active_session,
            lease_key=lease_key,
            operation=capture_operation,
        )
    except Exception as exc:
        logger.warning(
            "Agent decision capture failed closed for %s (%s)",
            task_return.return_id,
            type(exc).__name__,
        )


class EventLoopReviewMixin:
    """Provide review orchestration without expanding the scheduling core."""

    async def _review_in_process(self, record: Any) -> None:
        """Review one return off-loop and apply its bounded decision."""
        loop: Any = self
        reviewer = _selected_reviewer(loop)
        task_return = _task_return_from_record(record)
        attribution: _ReviewDecisionAttribution | None = None
        adapter = getattr(loop, "_decision_codification", None)
        if adapter is None:
            decision = await _review_or_manual_hold(loop, reviewer, task_return)
        else:
            decision, attribution = await _codified_review_or_manual_hold(
                loop,
                adapter,
                reviewer,
                record,
                task_return,
            )
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
        applied = await _apply_review_decision(
            loop,
            record,
            task_return,
            decision,
            promotion_receipt,
        )
        if adapter is not None:
            outcome = VerifiedOutcome.SUCCESS if applied else VerifiedOutcome.FAILURE
            await _record_codified_outcome(
                loop,
                adapter,
                attribution,
                outcome=outcome,
            )
            await _record_agent_decision_outcome(
                loop,
                adapter,
                attribution,
                record,
                task_return,
                decision,
                outcome=outcome,
            )
        if not applied:
            return
        await _write_review_audit(
            loop,
            record,
            decision,
            task_return.return_id,
            attribution,
        )
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
