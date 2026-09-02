"""Event-loop ordering for durable managed self-improvement promotion."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import TaskDecisionModel
from general_ludd.db.repository import ConcurrencyError
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.promotion import ManagedPromotionReceipt
from general_ludd.self_improve.staging import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
    ManagedSelfImprovePlanRequest,
)


def _receipt(repo_root: Path) -> ManagedPromotionReceipt:
    return ManagedPromotionReceipt(
        artifact_digest="a" * 64,
        plan_identity_digest="b" * 64,
        attempt_identity_digest="c" * 64,
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        repo_root=repo_root,
        return_id="RETURN-PROMOTION",
        development_commit="d" * 40,
        marker="Gludd-Self-Improve-Artifact=" + "a" * 64,
        fencing_token=1,
        marker_verified=True,
    )


def _return() -> SimpleNamespace:
    return SimpleNamespace(
        return_id="RETURN-PROMOTION",
        todo_id="TODO-PROMOTION",
        job_id="JOB-PROMOTION",
        playbook="self_improve.yml",
        queue="model",
        work_type="self_improve",
        exit_code=0,
        result_summary="result-json",
        project_id="project-promotion",
        status="claimed_for_review",
        updated_at=None,
    )


def _todo() -> SimpleNamespace:
    return SimpleNamespace(
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        work_type="self_improve",
        approval_policy=MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
        plan_artifact="plan-json",
        status=TodoStatus.REVIEWING_RETURN.value,
        version=2,
        title="Managed promotion",
        worktree=None,
    )


def _decision() -> TaskDecision:
    return TaskDecision(
        return_id="RETURN-PROMOTION",
        matched_todo_id="TODO-PROMOTION",
        decision="complete",
        confidence=1.0,
        evidence_refs=["artifact:proof.txt"],
    )


class _Reviewer:
    def review_return(self, *_args: Any, **_kwargs: Any) -> TaskDecision:
        return _decision()


async def test_review_persists_then_promotes_then_completes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    session = AsyncMock(spec=AsyncSession)
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = _todo()
    order: list[str] = []
    coordinator = AsyncMock()

    def promote(**_kwargs: Any) -> ManagedPromotionReceipt:
        order.append("promote")
        return _receipt(repo_root)

    coordinator.promote.side_effect = promote

    def factory(_session: AsyncSession, root: Path, owner: str) -> Any:
        assert root == repo_root
        assert "project-promotion" in owner
        return coordinator

    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=AsyncMock(),
        reviewer=_Reviewer(),
        self_improve_promotion_factory=factory,
    )

    async def persist(*_args: Any, **_kwargs: Any) -> None:
        order.append("persist")

    async def apply(*_args: Any, **kwargs: Any) -> None:
        assert kwargs["managed_promotion_receipt"].development_commit == "d" * 40
        order.append("apply")

    with (
        patch.object(loop, "_persist_in_process_decision", new=persist),
        patch("general_ludd.review.decision_applier.apply_decision", new=apply),
    ):
        await loop._review_in_process(_return())

    assert order == ["persist", "promote", "apply"]


async def test_promotion_failure_releases_review_claim_without_completing(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    session = AsyncMock(spec=AsyncSession)
    coordinator = AsyncMock()
    coordinator.promote.side_effect = RuntimeError("merge failed")
    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        todo_repo=AsyncMock(get_by_id=AsyncMock(return_value=_todo())),
        task_return_repo=AsyncMock(),
        reviewer=_Reviewer(),
        self_improve_promotion_factory=lambda *_args: coordinator,
    )
    persist_decision = AsyncMock()
    task_return = _return()

    with (
        patch.object(loop, "_persist_in_process_decision", new=persist_decision),
        patch(
            "general_ludd.review.decision_applier.apply_decision",
            new=AsyncMock(),
        ) as apply,
    ):
        await loop._review_in_process(task_return)

    assert task_return.status == "created"
    session.commit.assert_awaited()
    apply.assert_not_awaited()


async def test_in_process_decision_is_committed_before_external_effects() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    loop = EventLoop(session=session, todo_repo=AsyncMock(), task_return_repo=AsyncMock())

    await loop._persist_in_process_decision(_return(), _decision())

    persisted = session.add.call_args.args[0]
    assert isinstance(persisted, TaskDecisionModel)
    assert persisted.return_id == "RETURN-PROMOTION"
    assert json.loads(persisted.evidence_refs) == ["artifact:proof.txt"]
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_reconcile_requires_promotion_receipt_before_complete(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "proof.txt").write_text("ok", encoding="utf-8")
    session = MagicMock(spec=AsyncSession)
    decision = MagicMock(
        id=1,
        return_id="RETURN-PROMOTION",
        matched_todo_id="TODO-PROMOTION",
        decision="complete",
        confidence=1.0,
        project_id="project-promotion",
        evidence_refs=json.dumps(["artifact:proof.txt"]),
        audit_notes="[]",
    )
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [decision]
    session.execute.return_value = db_result
    todo_repo = AsyncMock()
    todo_repo.get_by_ids.return_value = {"TODO-PROMOTION": _todo()}
    task_return_repo = AsyncMock()
    task_return_repo.get_by_id.return_value = _return()
    order: list[str] = []
    todo_repo.transition.side_effect = lambda *_args, **_kwargs: order.append("transition")
    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )

    async def ensure(*_args: Any, **_kwargs: Any) -> ManagedPromotionReceipt:
        order.append("promote")
        return _receipt(repo_root)

    with (
        patch.object(loop, "_ensure_managed_self_improve_promotion", new=ensure),
        patch.object(
            loop,
            "_try_commit_completed_work",
            new=AsyncMock(return_value=False),
        ),
    ):
        await loop._phase_reconcile_completed_decisions()

    assert order == ["promote", "transition"]


async def test_persist_self_improve_todo_stages_exact_managed_plan_request() -> None:
    session = AsyncMock(spec=AsyncSession)
    todo_repo = AsyncMock()
    todo_repo.list_by_work_type.return_value = []
    loop = EventLoop(
        config={"self_improve": {}},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=AsyncMock(),
    )
    source = {
        "title": "Cover promotion retries",
        "description": "Exercise the durable promotion retry path",
        "source": "self_improve_harness",
        "gap_type": "missing_tests",
        "source_file": "src/general_ludd/self_improve/promotion.py",
        "work_type": "test",
        "task_type": "test_write",
        "test_commands": [
            "make test-files TESTFILES=tests/unit/test_managed_self_improve_promotion.py"
        ],
    }

    assert (
        await loop._persist_self_improve_todos(
            [source], project_id="project-promotion"
        )
        == 1
    )

    created = todo_repo.create.await_args.args[0]
    assert created["approval_policy"] == MANAGED_SELF_IMPROVE_APPROVAL_POLICY
    request = ManagedSelfImprovePlanRequest.from_json(created["plan_artifact"])
    assert request.project_id == "project-promotion"
    assert request.source_file == "src/general_ludd/self_improve/promotion.py"
    assert request.task.objective == "Exercise the durable promotion retry path"


async def test_unscoped_legacy_todo_persists_without_managed_artifact() -> None:
    session = AsyncMock(spec=AsyncSession)
    todo_repo = AsyncMock()
    todo_repo.list_by_work_type.return_value = []
    loop = EventLoop(session=session, todo_repo=todo_repo)

    persisted = await loop._persist_self_improve_todos(
        [{"title": "Unscoped change"}], project_id=None
    )

    assert persisted == 1
    created = todo_repo.create.await_args.args[0]
    assert "approval_policy" not in created
    assert "plan_artifact" not in created


def _persisted_decision() -> SimpleNamespace:
    decision = _decision()
    return SimpleNamespace(
        project_id="project-promotion",
        matched_todo_id=decision.matched_todo_id,
        decision=decision.decision,
        confidence=decision.confidence,
        evidence_refs=json.dumps(decision.evidence_refs),
        todo_updates=json.dumps(decision.todo_updates),
        child_todos=json.dumps(decision.child_todos),
        validation_requests=json.dumps(decision.validation_requests),
        git_requests=json.dumps(decision.git_requests),
        audit_notes=json.dumps(decision.audit_notes),
        policy_flags=json.dumps(decision.policy_flags),
    )


async def test_existing_identical_review_decision_is_idempotent() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = _persisted_decision()
    session.execute.return_value = result
    loop = EventLoop(session=session, todo_repo=AsyncMock(), task_return_repo=AsyncMock())

    await loop._persist_in_process_decision(_return(), _decision())

    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_awaited_once()


async def test_existing_review_decision_cannot_be_rebound() -> None:
    session = AsyncMock(spec=AsyncSession)
    existing = _persisted_decision()
    existing.audit_notes = json.dumps(["different"])
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute.return_value = result
    loop = EventLoop(session=session, todo_repo=AsyncMock(), task_return_repo=AsyncMock())

    with pytest.raises(ValueError, match="cannot be rebound"):
        await loop._persist_in_process_decision(_return(), _decision())

    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("session", "active session"),
        ("todo_id", "todo identity"),
        ("project_id", "project identity"),
        ("plan_artifact", "plan artifact"),
        ("result_summary", "result artifact"),
        ("return_id", "return identity"),
        ("repo_root", "repository root"),
        ("receipt", "invalid receipt"),
    ],
)
async def test_promotion_composition_fails_closed_on_missing_authority(
    tmp_path: Path,
    target: str,
    message: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    task_return = _return()
    todo = _todo()
    session: AsyncSession | None = AsyncMock(spec=AsyncSession)
    config: dict[str, object] = {"repo_root": str(repo_root)}
    coordinator = AsyncMock()
    coordinator.promote.return_value = _receipt(repo_root)
    if target == "session":
        session = None
    elif target == "todo_id":
        todo.todo_id = None
    elif target == "project_id":
        todo.project_id = None
    elif target == "plan_artifact":
        todo.plan_artifact = None
    elif target == "result_summary":
        task_return.result_summary = None
    elif target == "return_id":
        task_return.return_id = None
    elif target == "repo_root":
        config = {}
    else:
        coordinator.promote.return_value = object()
    loop = EventLoop(
        config=config,
        session=session,
        todo_repo=AsyncMock(),
        task_return_repo=AsyncMock(),
        self_improve_promotion_factory=lambda *_args: coordinator,
    )

    with pytest.raises((RuntimeError, ValueError), match=message):
        await loop._ensure_managed_self_improve_promotion(task_return, todo)


async def test_release_managed_review_without_session_is_a_noop() -> None:
    loop = EventLoop()
    task_return = _return()

    await loop._release_managed_review_for_retry(task_return)

    assert task_return.status == "claimed_for_review"


async def test_legacy_self_improve_review_does_not_enter_managed_promotion(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    session = AsyncMock(spec=AsyncSession)
    todo = _todo()
    todo.approval_policy = "none"
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = todo
    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=AsyncMock(),
        reviewer=_Reviewer(),
        self_improve_promotion_factory=lambda *_args: pytest.fail(
            "legacy todo must not construct a promotion coordinator"
        ),
    )
    persist_decision = AsyncMock()

    with (
        patch.object(loop, "_persist_in_process_decision", new=persist_decision),
        patch(
            "general_ludd.review.decision_applier.apply_decision",
            new=AsyncMock(),
        ) as apply,
    ):
        await loop._review_in_process(_return())

    persist_decision.assert_not_awaited()
    apply.assert_awaited_once()
    assert apply.await_args is not None
    assert apply.await_args.kwargs["managed_promotion_receipt"] is None


async def test_persisted_review_requires_active_session() -> None:
    loop = EventLoop()

    with pytest.raises(RuntimeError, match="active session"):
        await loop._persist_in_process_decision(_return(), _decision())


async def test_persisted_review_normalizes_non_string_project_identity() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    task_return = _return()
    task_return.project_id = 42
    loop = EventLoop(session=session, todo_repo=AsyncMock(), task_return_repo=AsyncMock())

    await loop._persist_in_process_decision(task_return, _decision())

    persisted = session.add.call_args.args[0]
    assert persisted.project_id is None


@pytest.mark.parametrize("failure", ["missing_todo", "decision_persistence"])
async def test_managed_review_reopens_claim_when_promotion_prerequisite_fails(
    tmp_path: Path,
    failure: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    session = AsyncMock(spec=AsyncSession)
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = None if failure == "missing_todo" else _todo()
    coordinator = AsyncMock()
    coordinator.promote.return_value = _receipt(repo_root)
    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=AsyncMock(),
        reviewer=_Reviewer(),
        self_improve_promotion_factory=lambda *_args: coordinator,
    )
    persist_decision = AsyncMock()
    if failure == "decision_persistence":
        persist_decision.side_effect = RuntimeError("database unavailable")
    task_return = _return()
    if failure == "missing_todo":
        task_return.project_id = 42

    with (
        patch.object(loop, "_persist_in_process_decision", new=persist_decision),
        patch(
            "general_ludd.review.decision_applier.apply_decision",
            new=AsyncMock(),
        ) as apply,
    ):
        await loop._review_in_process(task_return)

    assert task_return.status == "created"
    assert task_return.updated_at is not None
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    coordinator.promote.assert_not_awaited()
    apply.assert_not_awaited()


async def test_managed_review_does_not_complete_when_decision_apply_fails(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    session = AsyncMock(spec=AsyncSession)
    todo_repo = AsyncMock()
    todo_repo.get_by_id.return_value = _todo()
    coordinator = AsyncMock()
    coordinator.promote.return_value = _receipt(repo_root)
    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=AsyncMock(),
        reviewer=_Reviewer(),
        self_improve_promotion_factory=lambda *_args: coordinator,
    )

    with (
        patch.object(loop, "_persist_in_process_decision", new=AsyncMock()),
        patch(
            "general_ludd.review.decision_applier.apply_decision",
            new=AsyncMock(side_effect=RuntimeError("transition failed")),
        ),
    ):
        await loop._review_in_process(_return())

    coordinator.promote.assert_awaited_once()
    todo_repo.transition.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    ["missing_repository", "missing_return", "promotion_failure"],
)
async def test_reconcile_blocks_managed_complete_without_verified_promotion(
    tmp_path: Path,
    failure: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "proof.txt").write_text("ok", encoding="utf-8")
    session = MagicMock(spec=AsyncSession)
    decision = MagicMock(
        id=1,
        return_id="RETURN-PROMOTION",
        matched_todo_id="TODO-PROMOTION",
        decision="complete",
        confidence=1.0,
        project_id="project-promotion",
        evidence_refs=json.dumps(["artifact:proof.txt"]),
        audit_notes="[]",
    )
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [decision]
    session.execute.return_value = db_result
    todo_repo = AsyncMock()
    todo_repo.get_by_ids.return_value = {"TODO-PROMOTION": _todo()}
    task_return_repo = AsyncMock()
    if failure == "missing_return":
        task_return_repo.get_by_id.return_value = None
    elif failure == "promotion_failure":
        task_return_repo.get_by_id.return_value = _return()
    loop = EventLoop(
        config={"repo_root": str(repo_root)},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    if failure == "missing_repository":
        loop._task_return_repo = None
    ensure = AsyncMock()
    if failure == "promotion_failure":
        ensure.side_effect = RuntimeError("marker missing")

    with (
        patch.object(loop, "_ensure_managed_self_improve_promotion", new=ensure),
        patch.object(
            loop,
            "_try_commit_completed_work",
            new=AsyncMock(return_value=False),
        ),
    ):
        await loop._phase_reconcile_completed_decisions()

    todo_repo.transition.assert_not_awaited()
    if failure != "promotion_failure":
        ensure.assert_not_awaited()


async def test_self_improve_persistence_handles_capacity_and_storage_failures() -> None:
    full_session = AsyncMock(spec=AsyncSession)
    full_repo = AsyncMock()
    full_repo.list_by_work_type.return_value = [SimpleNamespace(status="queued")]
    full_loop = EventLoop(
        config={"self_improve": {"max_open": 1}},
        session=full_session,
        todo_repo=full_repo,
    )

    assert await full_loop._persist_self_improve_todos([{"title": "held"}]) == 0
    full_repo.create.assert_not_awaited()

    session = AsyncMock(spec=AsyncSession)
    session.flush.side_effect = RuntimeError("flush failed")
    todo_repo = AsyncMock()
    todo_repo.list_by_work_type.return_value = []
    todo_repo.create.side_effect = [None, RuntimeError("write failed")]
    loop = EventLoop(
        config={"self_improve": "invalid"},
        session=session,
        todo_repo=todo_repo,
    )

    persisted = await loop._persist_self_improve_todos(
        [
            {"title": "integer priority", "priority": 7},
            {"title": "failed write", "priority": "unexpected"},
        ]
    )

    assert persisted == 1
    assert todo_repo.create.await_args_list[0].args[0]["priority"] == 7
    session.flush.assert_awaited_once()


async def test_release_managed_review_without_updated_at_commits_created_state() -> None:
    session = AsyncMock(spec=AsyncSession)
    task_return = SimpleNamespace(status="claimed_for_review")
    loop = EventLoop(session=session)

    await loop._release_managed_review_for_retry(task_return)

    assert task_return.status == "created"
    assert not hasattr(task_return, "updated_at")
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_langgraph_review_records_audit_and_compaction_feedback() -> None:
    session = AsyncMock(spec=AsyncSession)
    langgraph_reviewer = MagicMock()
    reviewed = _decision().model_copy(update={"decision": "needs_more_work"})
    langgraph_reviewer.review_return.return_value = reviewed
    audit_repo = AsyncMock()
    compaction = MagicMock()
    compaction.compute.return_value = 2
    compaction.disable_signaled.return_value = True
    loop = EventLoop(
        config={
            "review": {"use_langgraph": True},
            "compaction": {"enabled": True, "level": 1},
        },
        session=session,
        todo_repo=AsyncMock(),
        reviewer=_Reviewer(),
        langgraph_reviewer=langgraph_reviewer,
        audit_repo=audit_repo,
        compaction_controller=compaction,
    )
    task_return = _return()
    task_return.work_type = "review"

    with patch(
        "general_ludd.review.decision_applier.apply_decision",
        new=AsyncMock(),
    ) as apply:
        await loop._review_in_process(task_return)
        compaction.compute.return_value = 2
        compaction.disable_signaled.return_value = False
        await loop._review_in_process(task_return)

    assert langgraph_reviewer.review_return.call_count == 2
    assert apply.await_count == 2
    assert audit_repo.create.await_count == 2
    assert compaction.compute.call_count == 2
    assert compaction.disable_signaled.call_count == 2
    assert loop._compaction_level == 2
    assert loop._compaction_disabled is False


async def test_review_failure_becomes_manual_hold_and_audit_failure_isolated() -> None:
    session = AsyncMock(spec=AsyncSession)
    reviewer = MagicMock()
    reviewer.review_return.side_effect = RuntimeError("review unavailable")
    audit_repo = AsyncMock()
    audit_repo.create.side_effect = RuntimeError("audit unavailable")
    loop = EventLoop(
        session=session,
        todo_repo=AsyncMock(),
        reviewer=reviewer,
        audit_repo=audit_repo,
    )
    task_return = _return()
    task_return.work_type = "review"

    with patch(
        "general_ludd.review.decision_applier.apply_decision",
        new=AsyncMock(),
    ) as apply:
        await loop._review_in_process(task_return)

    assert apply.await_args is not None
    decision = apply.await_args.args[0]
    assert decision.decision == "manual_hold"
    assert decision.audit_notes == ["Reviewer error: review unavailable"]
    audit_repo.create.assert_awaited_once()


def _persisted_reconcile_decision(
    *,
    decision: str = "complete",
    evidence_refs: str = '["artifact:proof.txt"]',
    confidence: float = 1.0,
) -> MagicMock:
    return MagicMock(
        id=77,
        return_id="RETURN-PROMOTION",
        matched_todo_id="TODO-PROMOTION",
        decision=decision,
        confidence=confidence,
        project_id="project-promotion",
        evidence_refs=evidence_refs,
        audit_notes="[]",
    )


def _reconcile_loop(
    decision: MagicMock,
    todo: SimpleNamespace,
    *,
    repo_root: Path | None = None,
    audit_repo: Any | None = None,
    ephemeral_account_manager: Any | None = None,
) -> tuple[EventLoop, Any, AsyncMock]:
    session = MagicMock(spec=AsyncSession)
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [decision]
    session.execute.return_value = db_result
    todo_repo = AsyncMock()
    todo_repo.get_by_ids.return_value = {todo.todo_id: todo}
    config = {"repo_root": str(repo_root)} if repo_root is not None else {}
    loop = EventLoop(
        config=config,
        session=session,
        todo_repo=todo_repo,
        task_return_repo=AsyncMock(),
        audit_repo=audit_repo,
        ephemeral_account_manager=ephemeral_account_manager,
    )
    return loop, session, todo_repo


async def test_reconcile_falls_back_to_individual_todo_lookup() -> None:
    decision = _persisted_reconcile_decision(decision="needs_more_work")
    todo = _todo()
    session = MagicMock(spec=AsyncSession)
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = [decision]
    session.execute.return_value = db_result
    todo_repo = AsyncMock()
    todo_repo.get_by_ids.return_value = []
    todo_repo.get_by_id.return_value = todo
    loop = EventLoop(session=session, todo_repo=todo_repo)

    await loop._phase_reconcile_completed_decisions()

    todo_repo.get_by_id.assert_awaited_once_with(
        "TODO-PROMOTION",
        project_id=None,
    )
    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.NEEDS_MORE_WORK,
        2,
    )


@pytest.mark.parametrize(
    (
        "decision_name",
        "already_pushed",
        "attempt_result",
        "expected_calls",
        "push_failures",
    ),
    [
        ("complete", False, True, 1, 1),
        ("complete", False, False, 1, 0),
        ("complete", True, True, 0, 0),
        ("needs_more_work", False, True, 0, 0),
    ],
)
async def test_reconcile_idempotency_ledger_handles_push_retry_branches(
    decision_name: str,
    already_pushed: bool,
    attempt_result: bool,
    expected_calls: int,
    push_failures: int,
) -> None:
    decision = _persisted_reconcile_decision(decision=decision_name)
    todo = _todo()
    loop, _session, todo_repo = _reconcile_loop(decision, todo)
    loop._applied_decisions["id:77"] = None
    if already_pushed:
        loop._pushed_work[todo.todo_id] = None
    attempt_push = AsyncMock(return_value=attempt_result)

    with patch.object(loop, "_attempt_completed_push", new=attempt_push):
        await loop._phase_reconcile_completed_decisions()

    assert attempt_push.await_count == expected_calls
    assert loop._tick_metrics["push_failures"] == push_failures
    todo_repo.transition.assert_not_awaited()


async def test_reconcile_malformed_completion_evidence_fails_closed() -> None:
    decision = _persisted_reconcile_decision(evidence_refs="not-json")
    todo = _todo()
    loop, _session, todo_repo = _reconcile_loop(decision, todo)

    await loop._phase_reconcile_completed_decisions()

    todo_repo.transition.assert_not_awaited()
    assert "id:77" not in loop._applied_decisions


async def test_reconcile_unknown_evidence_downgrade_does_not_transition() -> None:
    decision = _persisted_reconcile_decision()
    todo = _todo()
    todo.approval_policy = "none"
    loop, _session, todo_repo = _reconcile_loop(decision, todo)

    with patch.object(
        loop,
        "_bounded_to_thread",
        new=AsyncMock(return_value=SimpleNamespace(decision="unknown")),
    ):
        await loop._phase_reconcile_completed_decisions()

    todo_repo.transition.assert_not_awaited()


async def test_reconcile_unknown_raw_decision_does_not_transition() -> None:
    decision = _persisted_reconcile_decision(decision="unknown")
    todo = _todo()
    loop, _session, todo_repo = _reconcile_loop(decision, todo)

    await loop._phase_reconcile_completed_decisions()

    todo_repo.transition.assert_not_awaited()


async def test_reconcile_failed_project_gate_collects_check_summaries(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "project.yml").write_text("quality_gate: make gate\n", encoding="utf-8")
    (repo_root / "proof.txt").write_text("proof", encoding="utf-8")
    decision = _persisted_reconcile_decision()
    todo = _todo()
    todo.approval_policy = "none"
    loop, _session, todo_repo = _reconcile_loop(
        decision,
        todo,
        repo_root=repo_root,
    )
    gate_report = {
        "passed": False,
        "checks": [
            {"name": "lint", "passed": False, "summary": "lint failed"},
            {"name": "types", "passed": False},
            {"name": "tests", "passed": True},
            "malformed",
        ],
    }

    with patch.object(
        loop,
        "_bounded_to_thread",
        new=AsyncMock(
            side_effect=[
                SimpleNamespace(decision="complete"),
                gate_report,
            ]
        ),
    ):
        await loop._phase_reconcile_completed_decisions()

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.NEEDS_MORE_WORK,
        2,
    )


async def test_reconcile_failed_project_gate_without_check_list_still_downgrades(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "project.yml").write_text("quality_gate: make gate\n", encoding="utf-8")
    (repo_root / "proof.txt").write_text("proof", encoding="utf-8")
    decision = _persisted_reconcile_decision()
    todo = _todo()
    todo.approval_policy = "none"
    loop, _session, todo_repo = _reconcile_loop(
        decision,
        todo,
        repo_root=repo_root,
    )

    with patch.object(
        loop,
        "_bounded_to_thread",
        new=AsyncMock(
            side_effect=[
                SimpleNamespace(decision="complete"),
                {"passed": False, "checks": "invalid"},
            ]
        ),
    ):
        await loop._phase_reconcile_completed_decisions()

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.NEEDS_MORE_WORK,
        2,
    )


@pytest.mark.parametrize(
    ("approval", "expected_status"),
    [
        ("denied", TodoStatus.NEEDS_MORE_WORK),
        ("needs_more_work", TodoStatus.NEEDS_MORE_WORK),
        ("approved", TodoStatus.COMPLETE),
        (None, TodoStatus.COMPLETE),
    ],
)
async def test_reconcile_human_gate_decision_branches(
    tmp_path: Path,
    approval: str | None,
    expected_status: TodoStatus,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "proof.txt").write_text("proof", encoding="utf-8")
    decision = _persisted_reconcile_decision(confidence=0.2)
    todo = _todo()
    todo.approval_policy = "none"
    audit_repo = AsyncMock()
    ephemeral = object()
    loop, _session, todo_repo = _reconcile_loop(
        decision,
        todo,
        repo_root=repo_root,
        audit_repo=audit_repo,
        ephemeral_account_manager=ephemeral,
    )
    human_gate = MagicMock()
    human_gate.should_interrupt.return_value = True
    human_gate.await_approval = AsyncMock(return_value=approval)
    loop._human_gate = human_gate
    auto_record = AsyncMock()
    cleanup = AsyncMock()

    with (
        patch.object(
            loop,
            "_bounded_to_thread",
            new=AsyncMock(return_value=SimpleNamespace(decision="complete")),
        ),
        patch.object(loop, "_attempt_completed_push", new=AsyncMock(return_value=False)),
        patch.object(loop, "_auto_record_episode", new=auto_record),
        patch.object(loop, "_maybe_cleanup_ephemeral", new=cleanup),
    ):
        await loop._phase_reconcile_completed_decisions()
        await asyncio.sleep(0)

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        expected_status,
        2,
    )
    audit_repo.record_typed.assert_awaited_once()
    if expected_status == TodoStatus.COMPLETE:
        cleanup.assert_awaited_once()
    else:
        cleanup.assert_not_awaited()


async def test_reconcile_project_gate_error_is_fail_safe_and_audit_failure_isolated(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "project.yml").write_text("quality_gate: make gate\n", encoding="utf-8")
    (repo_root / "proof.txt").write_text("proof", encoding="utf-8")
    decision = _persisted_reconcile_decision()
    todo = _todo()
    todo.approval_policy = "none"
    audit_repo = AsyncMock()
    audit_repo.record_typed.side_effect = RuntimeError("audit unavailable")
    loop, _session, todo_repo = _reconcile_loop(
        decision,
        todo,
        repo_root=repo_root,
        audit_repo=audit_repo,
    )

    with (
        patch.object(
            loop,
            "_bounded_to_thread",
            new=AsyncMock(
                side_effect=[
                    SimpleNamespace(decision="complete"),
                    RuntimeError("gate unavailable"),
                ]
            ),
        ),
        patch.object(loop, "_attempt_completed_push", new=AsyncMock(return_value=False)),
        patch.object(loop, "_auto_record_episode", new=AsyncMock()),
    ):
        await loop._phase_reconcile_completed_decisions()
        await asyncio.sleep(0)

    todo_repo.transition.assert_awaited_once_with(
        "TODO-PROMOTION",
        TodoStatus.COMPLETE,
        2,
    )
    audit_repo.record_typed.assert_awaited_once()


async def test_reconcile_lost_version_race_is_not_marked_applied() -> None:
    decision = _persisted_reconcile_decision(decision="needs_more_work")
    todo = _todo()
    loop, _session, todo_repo = _reconcile_loop(decision, todo)
    todo_repo.transition.side_effect = ConcurrencyError("lost race")

    await loop._phase_reconcile_completed_decisions()

    assert "id:77" not in loop._applied_decisions
    assert loop._tick_metrics["decisions_applied"] == 0


async def test_completed_push_duplicate_and_backoff_paths_do_not_redeliver() -> None:
    todo = _todo()
    duplicate_loop = EventLoop()
    duplicate_loop._pushed_work[todo.todo_id] = None

    assert await duplicate_loop._attempt_completed_push(todo) is False

    backoff_loop = EventLoop()
    backoff_loop._push_retry_count[todo.todo_id] = 2
    window = 4
    offset = abs(hash(todo.todo_id)) % window
    backoff_loop._total_ticks = (1 - offset) % window
    commit = AsyncMock()
    with patch.object(backoff_loop, "_try_commit_completed_work", new=commit):
        assert await backoff_loop._attempt_completed_push(todo) is True
    commit.assert_not_awaited()


@pytest.mark.parametrize("failure", ["missing_repo", "missing_version", "transition"])
async def test_push_livelock_escape_is_best_effort(failure: str) -> None:
    todo = _todo()
    todo_repo: AsyncMock | None = AsyncMock()
    if failure == "missing_repo":
        todo_repo = None
    elif failure == "missing_version":
        todo.version = None
    else:
        assert todo_repo is not None
        todo_repo.transition.side_effect = RuntimeError("transition unavailable")
    loop = EventLoop(todo_repo=todo_repo)
    loop._push_retry_count[todo.todo_id] = 6

    await loop._escape_push_livelock(todo, 6, RuntimeError("push unavailable"))

    assert todo.todo_id not in loop._push_retry_count
    if failure == "transition":
        assert todo_repo is not None
        todo_repo.transition.assert_awaited_once()


async def test_service_credit_phase_records_low_balances_and_short_circuits_unknowns() -> None:
    tracker = MagicMock()
    tracker.check_all_balances.return_value = {
        "low": {"balance_usd": 1.0},
        "healthy": {"balance_usd": 20.0},
        "unknown": {"balance_usd": None},
    }
    tracker.should_refill.side_effect = lambda service: service == "low"
    daemon_state: dict[str, Any] = {}
    loop = EventLoop(
        config={"credit_check_interval_ticks": 1},
        credit_tracker=tracker,
        daemon_state=daemon_state,
    )
    loop._total_ticks = 1

    await loop._phase_check_service_credits()

    assert daemon_state["credits"] == tracker.check_all_balances.return_value
    assert loop._tick_metrics["low_credit_services"] == ["low"]
    assert tracker.should_refill.call_args_list == [
        (("low",), {}),
        (("healthy",), {}),
    ]


@pytest.mark.parametrize(
    "mode",
    ["disabled", "off_interval", "failure", "no_low_balance"],
)
async def test_service_credit_phase_handles_disabled_error_and_empty_low_paths(
    mode: str,
) -> None:
    tracker = MagicMock()
    config = {"credit_check_interval_ticks": 1}
    if mode == "disabled":
        config["credit_check_interval_ticks"] = 0
    elif mode == "off_interval":
        config["credit_check_interval_ticks"] = 2
    elif mode == "failure":
        tracker.check_all_balances.side_effect = RuntimeError("provider unavailable")
    else:
        tracker.check_all_balances.return_value = {
            "healthy": {"balance_usd": 20.0},
        }
        tracker.should_refill.return_value = False
    loop = EventLoop(config=config, credit_tracker=tracker)
    loop._total_ticks = 1

    await loop._phase_check_service_credits()

    assert "low_credit_services" not in loop._tick_metrics


async def test_compute_utilization_updates_floor_state_and_gpu_metrics() -> None:
    floor_controller = MagicMock()
    floor_controller.auto_tune.return_value = 3
    floor_controller.floor_history = [
        {"reason": "healthy_capacity", "previous_floor": 2},
    ]
    todo_repo = AsyncMock()
    todo_repo.status_summary.return_value = {"by_status": {"queued": "4"}}
    daemon_state: dict[str, Any] = {}
    loop = EventLoop(
        config={"compute_idle_check_interval_ticks": 1},
        todo_repo=todo_repo,
        floor_controller=floor_controller,
        daemon_state=daemon_state,
    )
    loop._total_ticks = 1
    loop._tick_state["claimed_todos"] = [object(), object()]
    loop._tick_metrics["todos_dispatched"] = 1

    with (
        patch("psutil.cpu_percent", return_value=10.0),
        patch("psutil.virtual_memory", return_value=SimpleNamespace(percent=20.0)),
        patch(
            "general_ludd.infra.gpu_metrics.GPUMetricsCollector.collect_all_gpu_metrics",
            return_value={"gpu": "healthy"},
        ),
    ):
        await loop._phase_check_compute_utilization()

    floor_controller.auto_tune.assert_called_once_with(
        cpu_pct=10.0,
        memory_pct=20.0,
        dispatch_success_rate=50.0,
        queue_depth=4,
    )
    assert daemon_state["floor_auto_tune"] == {"floor": 3, "history_size": 1}
    assert daemon_state["_last_gpu_metrics"] == {"gpu": "healthy"}


@pytest.mark.parametrize("failure", ["summary", "controller"])
async def test_compute_utilization_isolates_floor_failures(failure: str) -> None:
    floor_controller = MagicMock()
    floor_controller.auto_tune.return_value = 2
    floor_controller.floor_history = [{"reason": "no_change", "previous_floor": 2}]
    todo_repo = AsyncMock()
    if failure == "summary":
        todo_repo.status_summary.side_effect = RuntimeError("database unavailable")
    else:
        todo_repo.status_summary.return_value = {"by_status": {}}
        floor_controller.auto_tune.side_effect = RuntimeError("controller unavailable")
    loop = EventLoop(
        config={"compute_idle_check_interval_ticks": 1},
        todo_repo=todo_repo,
        floor_controller=floor_controller,
    )
    loop._total_ticks = 1

    with (
        patch("psutil.cpu_percent", return_value=10.0),
        patch("psutil.virtual_memory", return_value=SimpleNamespace(percent=20.0)),
        patch(
            "general_ludd.infra.gpu_metrics.GPUMetricsCollector.collect_all_gpu_metrics",
            return_value={},
        ),
    ):
        await loop._phase_check_compute_utilization()


@pytest.mark.parametrize("mode", ["disabled", "off_interval", "empty", "failure"])
async def test_spend_flush_phase_handles_retry_boundaries(mode: str) -> None:
    limiter = MagicMock()
    limiter.unflushed_records.return_value = []
    factory = MagicMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock()
    context.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = context
    config = {"spend_persist_interval_ticks": 2}
    if mode == "disabled":
        config["spend_persist_interval_ticks"] = 0
    loop = EventLoop(
        config=config,
        session=factory,
        spend_limiter=limiter,
    )
    loop._session_factory = factory
    loop._total_ticks = 1 if mode == "off_interval" else 2
    if mode == "failure":
        limiter.unflushed_records.return_value = [(1, 1.0, 2.0, None)]
        context.__aenter__.side_effect = RuntimeError("database unavailable")

    await loop._phase_flush_spend_ledger()

    limiter.mark_flushed.assert_not_called()


async def test_remediation_phase_missing_dependencies_and_failure_are_isolated() -> None:
    missing_loop = EventLoop(config={"remediation_check_interval_ticks": 1})
    missing_loop._total_ticks = 1
    await missing_loop._phase_remediate_blocked_tasks()

    todo_repo = AsyncMock()
    todo_repo.requeue_needs_more_work.side_effect = RuntimeError("database unavailable")
    failing_loop = EventLoop(
        config={"remediation_check_interval_ticks": 1},
        session=MagicMock(spec=AsyncSession),
        todo_repo=todo_repo,
    )
    failing_loop._total_ticks = 1
    await failing_loop._phase_remediate_blocked_tasks()
