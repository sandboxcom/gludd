"""Live return-review integration tests for deterministic decision codification."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.decision_codification.normalize import normalize_decision_context
from general_ludd.decision_codification.schema import (
    DecisionAbstentionV1,
    DecisionContextV1,
    DecisionKind,
    FallbackReason,
    NormalizationRefusalV1,
)
from general_ludd.decision_codification.service import (
    DecisionResolution,
    DecisionResolutionSource,
)
from general_ludd.event_loop.review_orchestration import EventLoopReviewMixin
from general_ludd.schemas.task_decision import TaskDecision

POLICY_DIGEST = "sha256:" + "e" * 64
CANDIDATE_DIGEST = "sha256:" + "a" * 64
RECEIPT_DIGEST = "sha256:" + "b" * 64


class _Session:
    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


class _AuditRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> None:
        self.events.append(kwargs)


class _Reviewer:
    def __init__(self, result: TaskDecision | Exception) -> None:
        self.result = result
        self.calls = 0
        self.thread_ids: list[int] = []

    def review_return(self, *_args: object, **_kwargs: object) -> TaskDecision:
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Loop(EventLoopReviewMixin):
    def __init__(self, reviewer: _Reviewer, adapter: object | None) -> None:
        self.config: dict[str, object] = {}
        self._reviewer = reviewer
        self._consensus_reviewer = None
        self._langgraph_reviewer = None
        self._decision_codification = adapter
        self._todo_repo = object()
        self._active_session = _Session()
        self._audit_repo = _AuditRepository()
        self._compaction_controller = None
        self.offloaded: list[object] = []

    async def _bounded_to_thread(
        self,
        func: Any,
        *args: object,
        **kwargs: object,
    ) -> object:
        self.offloaded.append(func)
        return await asyncio.to_thread(func, *args, **kwargs)

    def _resolve_repo_root(self, _project_id: str | None) -> Path:
        return Path(".")


class _HitAdapter:
    def __init__(
        self,
        action: str,
        *,
        candidate_digest: str | None = CANDIDATE_DIGEST,
        receipt_digest: str | None = RECEIPT_DIGEST,
    ) -> None:
        self.action = action
        self.candidate_digest = candidate_digest
        self.receipt_digest = receipt_digest
        self.calls: list[dict[str, object]] = []

    def resolve(self, **kwargs: object) -> DecisionResolution:
        self.calls.append(kwargs)
        return DecisionResolution(
            decision=self.action,
            source=DecisionResolutionSource.CODIFIED,
            candidate_digest=self.candidate_digest,
            decision_receipt_digest=self.receipt_digest,
            abstention=None,
        )


class _AbstainingAdapter:
    def __init__(self, abstention: DecisionAbstentionV1) -> None:
        self.abstention = abstention
        self.calls: list[dict[str, object]] = []
        self.fallback_actions: list[str] = []

    def resolve(self, **kwargs: object) -> DecisionResolution:
        self.calls.append(kwargs)
        fallback = kwargs["fallback"]
        action = fallback(self.abstention)  # type: ignore[operator]
        self.fallback_actions.append(action)
        return DecisionResolution(
            decision=action,
            source=DecisionResolutionSource.AGENT_FALLBACK,
            candidate_digest=self.abstention.candidate_digest,
            decision_receipt_digest=None,
            abstention=self.abstention,
        )


class _NormalizingAdapter:
    """Small adapter double that preserves the production normalizer boundary."""

    def __init__(self, *, project_id: str = "project-1") -> None:
        self.project_id = project_id
        self.calls: list[dict[str, object]] = []

    def resolve(self, **kwargs: object) -> DecisionResolution:
        self.calls.append(kwargs)
        normalized = normalize_decision_context(
            project_id=kwargs["project_id"],  # type: ignore[arg-type]
            expected_project_id=self.project_id,
            decision_kind=kwargs["decision_kind"],  # type: ignore[arg-type]
            policy_digest=POLICY_DIGEST,
            features=kwargs["features"],
        )
        if isinstance(normalized, DecisionContextV1):
            return DecisionResolution(
                decision="approve",
                source=DecisionResolutionSource.CODIFIED,
                candidate_digest=CANDIDATE_DIGEST,
                decision_receipt_digest=RECEIPT_DIGEST,
                abstention=None,
            )
        assert isinstance(normalized, NormalizationRefusalV1)
        abstention = DecisionAbstentionV1(
            reason=FallbackReason.NORMALIZATION_REFUSED,
            normalization_reason=normalized.reason,
        )
        fallback = kwargs["fallback"]
        action = fallback(abstention)  # type: ignore[operator]
        return DecisionResolution(
            decision=action,
            source=DecisionResolutionSource.AGENT_FALLBACK,
            candidate_digest=None,
            decision_receipt_digest=None,
            abstention=abstention,
        )


class _RaisingAdapter:
    def resolve(self, **_kwargs: object) -> DecisionResolution:
        raise RuntimeError("sensitive runtime detail")


class _InvalidResolutionAdapter:
    def resolve(self, **_kwargs: object) -> object:
        return object()


class _InconsistentFallbackAdapter:
    def resolve(self, **_kwargs: object) -> DecisionResolution:
        return DecisionResolution(
            decision="reject",
            source=DecisionResolutionSource.AGENT_FALLBACK,
            candidate_digest=None,
            decision_receipt_digest=None,
            abstention=DecisionAbstentionV1(reason=FallbackReason.NO_ACTIVE_RULE),
        )


def _record(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "return_id": "RET-LIVE-001",
        "todo_id": "TODO-LIVE-001",
        "job_id": "JOB-LIVE-001",
        "project_id": "project-1",
        "playbook": "noop.yml",
        "queue": "batch",
        "work_type": "code",
        "risk_band": "low",
        "resource_profile": "cpu",
        "provider_class": "local",
        "exit_code": 0,
        "result_summary": "must never enter codification audit attribution",
        "approval_required": False,
        "reversible": True,
        "retry_count": 0,
        "estimated_cost_microusd": 50_000,
        "latency_ms": 250,
        "required_evidence": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _capture_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> list[TaskDecision]:
    applied: list[TaskDecision] = []

    async def _apply(decision: TaskDecision, *_args: object, **_kwargs: object) -> None:
        applied.append(decision)

    monkeypatch.setattr(
        "general_ludd.review.decision_applier.apply_decision",
        _apply,
    )
    return applied


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "task_decision"),
    [
        ("approve", "complete"),
        ("request_changes", "needs_more_work"),
        ("reject", "failed"),
    ],
)
async def test_exact_review_hit_skips_reviewer_and_maps_closed_action(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    task_decision: str,
) -> None:
    applied = await _capture_applied(monkeypatch)
    adapter = _HitAdapter(action)
    reviewer = _Reviewer(RuntimeError("reviewer must not run"))
    loop = _Loop(reviewer, adapter)

    await loop._review_in_process(_record())

    assert reviewer.calls == 0
    assert len(loop.offloaded) == 1
    assert [item.decision for item in applied] == [task_decision]
    assert applied[0].return_id == "RET-LIVE-001"
    assert applied[0].matched_todo_id == "TODO-LIVE-001"
    assert adapter.calls[0]["decision_kind"] is DecisionKind.REVIEW
    assert adapter.calls[0]["project_id"] == "project-1"
    assert adapter.calls[0]["correlation_id"] == "return-review:RET-LIVE-001"
    assert adapter.calls[0]["side_effect_id"] == "task-decision:RET-LIVE-001"
    details = json.loads(loop._audit_repo.events[0]["details"])  # type: ignore[arg-type]
    assert details == {
        "decision": task_decision,
        "confidence": 1.0,
        "matched_todo_id": "TODO-LIVE-001",
        "decision_source": "codified",
        "candidate_digest": CANDIDATE_DIGEST,
        "decision_receipt_digest": RECEIPT_DIGEST,
    }
    assert "result_summary" not in details
    assert "features" not in details


@pytest.mark.asyncio
async def test_codified_lookup_uses_repeatable_idempotency_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _capture_applied(monkeypatch)
    adapter = _HitAdapter("request_changes")
    reviewer = _Reviewer(RuntimeError("reviewer must not run"))
    loop = _Loop(reviewer, adapter)
    record = _record()

    await loop._review_in_process(record)
    await loop._review_in_process(record)

    assert reviewer.calls == 0
    assert len(adapter.calls) == 2
    assert adapter.calls[0]["correlation_id"] == adapter.calls[1]["correlation_id"]
    assert adapter.calls[0]["side_effect_id"] == adapter.calls[1]["side_effect_id"]
    assert adapter.calls[0]["features"] == adapter.calls[1]["features"]


@pytest.mark.asyncio
async def test_abstention_reviews_once_off_loop_and_preserves_full_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = await _capture_applied(monkeypatch)
    fallback_decision = TaskDecision(
        return_id="RET-LIVE-001",
        matched_todo_id="TODO-LIVE-001",
        decision="manual_hold",
        confidence=0.37,
        evidence_refs=["sha256:evidence"],
        todo_updates={"status": "blocked"},
        audit_notes=["operator review required"],
    )
    reviewer = _Reviewer(fallback_decision)
    adapter = _AbstainingAdapter(
        DecisionAbstentionV1(
            reason=FallbackReason.SCOPE_MISS,
            candidate_digest=CANDIDATE_DIGEST,
        )
    )
    loop = _Loop(reviewer, adapter)
    event_loop_thread = threading.get_ident()

    await loop._review_in_process(_record())

    assert reviewer.calls == 1
    assert reviewer.thread_ids != [event_loop_thread]
    assert adapter.fallback_actions == ["reject"]
    assert applied == [fallback_decision]
    details = json.loads(loop._audit_repo.events[0]["details"])  # type: ignore[arg-type]
    assert details["decision_source"] == "agent_fallback"
    assert details["fallback_reason"] == "scope_miss"
    assert details["candidate_digest"] == CANDIDATE_DIGEST
    assert "normalization_reason" not in details


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"risk_band": None},
        {"risk_band": "high"},
        {"work_type": "unsupported-work"},
        {"project_id": "project-2"},
        {"work_type": "self_improve"},
        {"todo_id": None},
    ],
)
async def test_ineligible_context_fails_closed_to_exactly_one_review(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    applied = await _capture_applied(monkeypatch)
    reviewer_decision = TaskDecision(
        return_id="RET-LIVE-001",
        matched_todo_id="TODO-LIVE-001",
        decision="manual_hold",
        confidence=0.0,
    )
    reviewer = _Reviewer(reviewer_decision)
    adapter = _NormalizingAdapter()
    loop = _Loop(reviewer, adapter)

    await loop._review_in_process(_record(**overrides))

    assert reviewer.calls == 1
    assert applied == [reviewer_decision]
    details = json.loads(loop._audit_repo.events[0]["details"])  # type: ignore[arg-type]
    assert details["decision_source"] == "agent_fallback"
    assert details["fallback_reason"] == "normalization_refused"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter",
    [
        _HitAdapter("allow"),
        _HitAdapter("approve", candidate_digest=None),
        _RaisingAdapter(),
        _InvalidResolutionAdapter(),
        _InconsistentFallbackAdapter(),
    ],
)
async def test_invalid_action_or_adapter_error_falls_back_once_without_content_leak(
    monkeypatch: pytest.MonkeyPatch,
    adapter: object,
) -> None:
    applied = await _capture_applied(monkeypatch)
    reviewer_decision = TaskDecision(
        return_id="RET-LIVE-001",
        matched_todo_id="TODO-LIVE-001",
        decision="blocked",
        confidence=0.4,
    )
    reviewer = _Reviewer(reviewer_decision)
    loop = _Loop(reviewer, adapter)

    await loop._review_in_process(_record())

    assert reviewer.calls == 1
    assert applied == [reviewer_decision]
    details = json.loads(loop._audit_repo.events[0]["details"])  # type: ignore[arg-type]
    assert details["decision_source"] == "agent_fallback"
    assert details["fallback_reason"] == "runtime_error"
    assert "sensitive runtime detail" not in loop._audit_repo.events[0]["details"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_reviewer_error_on_abstention_keeps_manual_hold_and_single_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = await _capture_applied(monkeypatch)
    reviewer = _Reviewer(RuntimeError("review failed"))
    adapter = _AbstainingAdapter(
        DecisionAbstentionV1(reason=FallbackReason.NO_ACTIVE_RULE)
    )
    loop = _Loop(reviewer, adapter)

    await loop._review_in_process(_record())

    assert reviewer.calls == 1
    assert len(applied) == 1
    assert applied[0].decision == "manual_hold"
    assert applied[0].matched_todo_id == "TODO-LIVE-001"
    assert applied[0].audit_notes == ["Reviewer error: review failed"]


@pytest.mark.asyncio
async def test_no_adapter_retains_original_review_and_audit_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied = await _capture_applied(monkeypatch)
    decision = TaskDecision(
        return_id="RET-LIVE-001",
        matched_todo_id="TODO-LIVE-001",
        decision="needs_more_work",
        confidence=0.73,
        evidence_refs=["evidence-ref"],
        validation_requests=["run focused test"],
    )
    reviewer = _Reviewer(decision)
    loop = _Loop(reviewer, None)

    await loop._review_in_process(_record())

    assert reviewer.calls == 1
    assert applied == [decision]
    details = json.loads(loop._audit_repo.events[0]["details"])  # type: ignore[arg-type]
    assert details == {
        "decision": "needs_more_work",
        "confidence": 0.73,
        "matched_todo_id": "TODO-LIVE-001",
    }
