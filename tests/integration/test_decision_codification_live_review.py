"""Integration proof for an approved rule in the live return-review path."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.decision_codification.service import DecisionCodificationAdapter
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision
from tests.integration.test_decision_codification_app_integration import (
    NOW,
    POLICY_DIGEST,
    _active_runtime,
    _RejectingVerifiedReader,
)


def _set_review_time(monkeypatch: pytest.MonkeyPatch, current: datetime) -> None:
    """Own the same UTC clock boundary used by the live review resolver."""
    class ReviewClock:
        @staticmethod
        def now(tz: object = None) -> datetime:
            assert tz is UTC
            return current

    monkeypatch.setattr(
        "general_ludd.event_loop.review_orchestration.datetime",
        ReviewClock,
    )


@pytest.mark.asyncio
async def test_approved_rule_short_circuits_live_reviewer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The real adapter/runtime must reach apply_decision with zero model calls."""
    _set_review_time(monkeypatch, NOW + timedelta(minutes=1))
    adapter = DecisionCodificationAdapter(
        bundle_reader=_RejectingVerifiedReader(),
        runtime=_active_runtime(tmp_path),
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
    )
    reviewer = MagicMock()
    session = AsyncMock()
    session.flush = AsyncMock()
    todo_repo = AsyncMock()
    audit_repo = AsyncMock()
    loop = EventLoop(
        reviewer=reviewer,
        session=session,
        todo_repo=todo_repo,
        audit_repo=audit_repo,
        decision_codification=adapter,
    )
    applied: list[TaskDecision] = []

    async def _apply(decision: TaskDecision, *_args: object, **_kwargs: object) -> None:
        applied.append(decision)

    monkeypatch.setattr(
        "general_ludd.review.decision_applier.apply_decision",
        _apply,
    )
    record = SimpleNamespace(
        return_id="RET-LIVE-INTEGRATION",
        todo_id="TODO-LIVE-INTEGRATION",
        job_id="JOB-LIVE-INTEGRATION",
        project_id="project-1",
        playbook="noop.yml",
        queue="batch",
        work_type="code",
        risk_band="low",
        resource_profile="cpu",
        provider_class="local",
        exit_code=0,
        result_summary="content stays outside codification attribution",
        approval_required=False,
        reversible=True,
        retry_count=0,
        estimated_cost_microusd=50_000,
        latency_ms=250,
        required_evidence=True,
    )

    await loop._review_in_process(record)

    reviewer.review_return.assert_not_called()
    assert len(applied) == 1
    assert applied[0].decision == "complete"
    assert applied[0].matched_todo_id == "TODO-LIVE-INTEGRATION"
    assert len(applied[0].evidence_refs) == 1
    audit = audit_repo.create.await_args.kwargs
    details = json.loads(audit["details"])
    assert details["decision_source"] == "codified"
    assert details["candidate_digest"].startswith("sha256:")
    assert details["decision_receipt_digest"].startswith("sha256:")
    assert "result_summary" not in details


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("review_time", "expired"),
    [
        pytest.param(
            NOW + timedelta(days=30) - timedelta(microseconds=1),
            False,
            id="before-approval-expiry",
        ),
        pytest.param(NOW + timedelta(days=30), True, id="at-approval-expiry"),
        pytest.param(NOW + timedelta(days=32), True, id="after-approval-expiry"),
    ],
)
async def test_live_approval_expiry_uses_owned_clock_and_one_valid_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    review_time: datetime,
    expired: bool,
) -> None:
    """The unchanged expiry gate selects reuse or one complete reviewer result."""
    _set_review_time(monkeypatch, review_time)
    adapter = DecisionCodificationAdapter(
        bundle_reader=_RejectingVerifiedReader(),
        runtime=_active_runtime(tmp_path),
        project_id="project-1",
        policy_digest=POLICY_DIGEST,
    )
    reviewed = TaskDecision(
        return_id="RET-EXPIRY-INTEGRATION",
        matched_todo_id="TODO-EXPIRY-INTEGRATION",
        decision="manual_hold",
        confidence=0.37,
        evidence_refs=["operator-review-proof"],
        todo_updates={"status": "blocked"},
        audit_notes=["Approval expired; live review required."],
    )
    reviewer = MagicMock()
    reviewer.review_return.return_value = reviewed
    session = AsyncMock()
    session.flush = AsyncMock()
    todo_repo = AsyncMock()
    audit_repo = AsyncMock()
    loop = EventLoop(
        reviewer=reviewer,
        session=session,
        todo_repo=todo_repo,
        audit_repo=audit_repo,
        decision_codification=adapter,
    )
    applied: list[TaskDecision] = []

    async def _apply(decision: TaskDecision, *_args: object, **_kwargs: object) -> None:
        applied.append(decision)

    monkeypatch.setattr(
        "general_ludd.review.decision_applier.apply_decision",
        _apply,
    )
    record = SimpleNamespace(
        return_id="RET-EXPIRY-INTEGRATION",
        todo_id="TODO-EXPIRY-INTEGRATION",
        job_id="JOB-EXPIRY-INTEGRATION",
        project_id="project-1",
        playbook="noop.yml",
        queue="batch",
        work_type="code",
        risk_band="low",
        resource_profile="cpu",
        provider_class="local",
        exit_code=0,
        result_summary="content stays outside expiry attribution",
        approval_required=False,
        reversible=True,
        retry_count=0,
        estimated_cost_microusd=50_000,
        latency_ms=250,
        required_evidence=True,
    )

    await loop._review_in_process(record)

    assert len(applied) == 1
    audit_repo.create.assert_awaited_once()
    audit = audit_repo.create.await_args.kwargs
    details = json.loads(audit["details"])
    assert details["candidate_digest"].startswith("sha256:")
    assert "result_summary" not in details
    assert record.result_summary not in audit["details"]
    if expired:
        reviewer.review_return.assert_called_once()
        reviewed_return = reviewer.review_return.call_args.args[0]
        assert reviewed_return.return_id == record.return_id
        assert reviewed_return.todo_id == record.todo_id
        assert reviewer.review_return.call_args.kwargs == {
            "candidate_todos": [],
            "artifacts": [],
        }
        assert applied[0] is reviewed
        assert details["decision_source"] == "agent_fallback"
        assert details["fallback_reason"] == "expired"
        assert "decision_receipt_digest" not in details
    else:
        reviewer.review_return.assert_not_called()
        assert applied[0].decision == "complete"
        assert applied[0].matched_todo_id == record.todo_id
        assert details["decision_source"] == "codified"
        assert details["decision_receipt_digest"].startswith("sha256:")
        assert "fallback_reason" not in details
