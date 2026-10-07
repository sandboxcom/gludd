"""Integration proof for an approved rule in the live return-review path."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.decision_codification.service import DecisionCodificationAdapter
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision
from tests.integration.test_decision_codification_app_integration import (
    POLICY_DIGEST,
    _active_runtime,
    _RejectingVerifiedReader,
)


@pytest.mark.asyncio
async def test_approved_rule_short_circuits_live_reviewer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The real adapter/runtime must reach apply_decision with zero model calls."""
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
