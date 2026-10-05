"""Behavioral coverage for the extracted decision-reconciliation policy."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

import general_ludd.event_loop.decision_reconciliation as subject
from general_ludd.db.repository import ConcurrencyError
from general_ludd.schemas.todo import TodoStatus


def _decision(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "decision-1",
        "return_id": "return-1",
        "matched_todo_id": "todo-1",
        "decision": "needs_more_work",
        "confidence": 0.9,
        "project_id": "project-1",
        "evidence_refs": "[]",
        "audit_notes": "[]",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _todo(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "todo_id": "todo-1",
        "project_id": "project-1",
        "status": TodoStatus.REVIEWING_RETURN.value,
        "version": 4,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _scalar_result(*rows: object) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    return result


@pytest.mark.asyncio
async def test_reconcile_batches_decisions_and_records_both_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The phase counts applied decisions and retryable push failures separately."""
    decisions = [_decision(id="decision-1"), _decision(id="decision-2")]
    loop = SimpleNamespace(
        _active_session=SimpleNamespace(execute=AsyncMock(return_value=_scalar_result(*decisions))),
        _todo_repo=object(),
        _tick_project_id="project-1",
        _tick_metrics={},
    )
    load_todos = AsyncMock(return_value={"todo-1": _todo()})
    reconcile_one = AsyncMock(side_effect=[(True, False), (False, True)])
    monkeypatch.setattr(subject, "_load_todos", load_todos)
    monkeypatch.setattr(subject, "_reconcile_one", reconcile_one)

    await subject.reconcile_completed_decisions(loop)

    assert loop._tick_metrics == {"decisions_applied": 1, "push_failures": 1}
    load_todos.assert_awaited_once_with(loop, decisions, "project-1")
    assert reconcile_one.await_count == 2


@pytest.mark.asyncio
async def test_reconcile_and_loading_short_circuit_without_required_state() -> None:
    """Absent session/repository state and decisions without todo IDs are no-ops."""
    session = SimpleNamespace(execute=AsyncMock(return_value=_scalar_result()))
    await subject.reconcile_completed_decisions(
        SimpleNamespace(_active_session=None, _todo_repo=object())
    )
    await subject.reconcile_completed_decisions(
        SimpleNamespace(_active_session=session, _todo_repo=None)
    )

    repo_without_point_reads = SimpleNamespace(get_by_ids=AsyncMock(return_value=[]))
    assert (
        await subject._load_todos(
            SimpleNamespace(_todo_repo=repo_without_point_reads),
            [_decision(matched_todo_id=None)],
            None,
        )
        == {}
    )
    assert (
        await subject._load_todos(
            SimpleNamespace(_todo_repo=repo_without_point_reads),
            [_decision()],
            None,
        )
        == {}
    )


@pytest.mark.asyncio
async def test_load_todos_accepts_batch_mapping_and_legacy_point_reads() -> None:
    """Both modern mapping repositories and legacy point-read repositories work."""
    decisions = [_decision(matched_todo_id="todo-1"), _decision(matched_todo_id="todo-2")]
    first = _todo(todo_id="todo-1")
    mapping_repo = SimpleNamespace(get_by_ids=AsyncMock(return_value={"todo-1": first}))

    assert await subject._load_todos(
        SimpleNamespace(_todo_repo=mapping_repo), decisions, "project-1"
    ) == {"todo-1": first}

    second = _todo(todo_id="todo-2")
    legacy_repo = SimpleNamespace(
        get_by_ids=AsyncMock(return_value=[first, second]),
        get_by_id=AsyncMock(side_effect=[first, second]),
    )
    loaded = await subject._load_todos(
        SimpleNamespace(_todo_repo=legacy_repo), decisions, "project-1"
    )

    assert loaded == {"todo-1": first, "todo-2": second}
    assert legacy_repo.get_by_id.await_count == 2


@pytest.mark.asyncio
async def test_reconcile_one_retries_only_unpushed_completed_work() -> None:
    """An applied completion remains idempotent while its failed push is retried."""
    decision = _decision(decision="complete")
    todo = _todo()
    loop = SimpleNamespace(
        _decision_id=lambda _decision: "decision-1",
        _applied_decisions={"decision-1"},
        _pushed_work=set(),
        _attempt_completed_push=AsyncMock(return_value=True),
    )

    assert await subject._reconcile_one(loop, decision, {"todo-1": todo}) == (
        False,
        True,
    )
    loop._attempt_completed_push.assert_awaited_once_with(todo)

    loop._pushed_work.add("todo-1")
    assert await subject._reconcile_one(loop, decision, {"todo-1": todo}) == (
        False,
        False,
    )


@pytest.mark.asyncio
async def test_reconcile_one_rejects_ineligible_rows_and_commits_normal_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a recognized decision for a reviewing todo reaches the transition."""
    loop = SimpleNamespace(
        _decision_id=lambda _decision: "decision-1",
        _applied_decisions=set(),
        _decision_to_status=lambda value: (
            TodoStatus.NEEDS_MORE_WORK if value == "needs_more_work" else None
        ),
    )
    assert await subject._reconcile_one(
        loop, _decision(matched_todo_id=None), {}
    ) == (False, False)
    assert await subject._reconcile_one(loop, _decision(), {}) == (False, False)
    assert await subject._reconcile_one(
        loop,
        _decision(),
        {"todo-1": _todo(status=TodoStatus.COMPLETE.value)},
    ) == (False, False)
    assert await subject._reconcile_one(
        loop,
        _decision(decision="unknown"),
        {"todo-1": _todo()},
    ) == (False, False)

    commit = AsyncMock(return_value=(True, False))
    monkeypatch.setattr(subject, "_commit_transition", commit)
    todo = _todo()
    assert await subject._reconcile_one(
        loop, _decision(), {"todo-1": todo}
    ) == (True, False)
    commit.assert_awaited_once_with(
        loop,
        ANY,
        todo,
        "decision-1",
        TodoStatus.NEEDS_MORE_WORK,
    )


@pytest.mark.asyncio
async def test_reconcile_one_blocks_complete_when_managed_promotion_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed self-improvement work cannot complete without a verified receipt."""
    decision = _decision(decision="complete")
    todo = _todo()
    loop = SimpleNamespace(
        _decision_id=lambda _decision: "decision-1",
        _applied_decisions=set(),
        _decision_to_status=lambda _decision: TodoStatus.COMPLETE,
    )
    monkeypatch.setattr(
        subject,
        "_verify_completion_evidence",
        AsyncMock(return_value=(TodoStatus.COMPLETE, "/repo")),
    )
    monkeypatch.setattr(
        subject, "_apply_project_gate", AsyncMock(return_value=TodoStatus.COMPLETE)
    )
    monkeypatch.setattr(
        subject, "_apply_human_gate", AsyncMock(return_value=TodoStatus.COMPLETE)
    )
    monkeypatch.setattr(subject, "is_managed_self_improve_todo", lambda _todo: True)
    monkeypatch.setattr(subject, "_promote_managed_todo", AsyncMock(return_value=False))
    commit = AsyncMock(return_value=(True, False))
    monkeypatch.setattr(subject, "_commit_transition", commit)

    assert await subject._reconcile_one(loop, decision, {"todo-1": todo}) == (
        False,
        False,
    )
    commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_evidence_rejects_malformed_payload() -> None:
    """Malformed persisted JSON is rejected before any verifier is invoked."""
    decision = _decision(decision="complete", evidence_refs="not-json")
    loop = SimpleNamespace()

    assert (
        await subject._verify_completion_evidence(
            loop, decision, _todo(), "decision-1"
        )
        is None
    )


@pytest.mark.asyncio
async def test_completion_evidence_propagates_verifier_downgrade() -> None:
    """A valid payload adopts the completion verifier's safer decision."""
    loop = SimpleNamespace(
        _resolve_repo_root=lambda _project_id: "/repo",
        _bounded_to_thread=AsyncMock(
            return_value=SimpleNamespace(decision="needs_more_work")
        ),
        _decision_to_status=lambda value: (
            TodoStatus.NEEDS_MORE_WORK if value == "needs_more_work" else None
        ),
    )

    assert await subject._verify_completion_evidence(
        loop, _decision(decision="complete"), _todo(), "decision-1"
    ) == (TodoStatus.NEEDS_MORE_WORK, "/repo")
    loop._bounded_to_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_and_human_gates_downgrade_failed_completion(
    tmp_path: Path,
) -> None:
    """A failed project check or explicit human denial yields NEEDS_MORE_WORK."""
    (tmp_path / "project.yml").write_text("name: fixture\n", encoding="utf-8")
    loop = SimpleNamespace(
        _bounded_to_thread=AsyncMock(
            return_value={
                "passed": False,
                "checks": [{"name": "tests", "passed": False, "summary": "red"}],
            }
        ),
        _human_gate=SimpleNamespace(
            should_interrupt=lambda _confidence: True,
            await_approval=AsyncMock(return_value="denied"),
        ),
    )

    project_status = await subject._apply_project_gate(
        loop, "decision-1", str(tmp_path), TodoStatus.COMPLETE
    )
    human_status = await subject._apply_human_gate(
        loop,
        _decision(decision="complete"),
        _todo(),
        "decision-1",
        TodoStatus.COMPLETE,
    )

    assert project_status is TodoStatus.NEEDS_MORE_WORK
    assert human_status is TodoStatus.NEEDS_MORE_WORK


@pytest.mark.asyncio
async def test_project_and_human_gates_preserve_safe_statuses(tmp_path: Path) -> None:
    """Missing project policy, gate errors, and human approval preserve the status."""
    loop = SimpleNamespace(
        _bounded_to_thread=AsyncMock(side_effect=RuntimeError("gate unavailable")),
        _human_gate=SimpleNamespace(
            should_interrupt=lambda _confidence: True,
            await_approval=AsyncMock(return_value="approved"),
        ),
    )
    assert await subject._apply_project_gate(
        loop, "decision-1", None, TodoStatus.COMPLETE
    ) is TodoStatus.COMPLETE
    assert await subject._apply_project_gate(
        loop, "decision-1", str(tmp_path), TodoStatus.COMPLETE
    ) is TodoStatus.COMPLETE

    (tmp_path / "project.yml").write_text("name: fixture\n", encoding="utf-8")
    assert await subject._apply_project_gate(
        loop, "decision-1", str(tmp_path), TodoStatus.COMPLETE
    ) is TodoStatus.COMPLETE
    assert await subject._apply_human_gate(
        loop,
        _decision(decision="complete"),
        _todo(),
        "decision-1",
        TodoStatus.COMPLETE,
    ) is TodoStatus.COMPLETE

    loop._human_gate.should_interrupt = lambda _confidence: False
    assert await subject._apply_human_gate(
        loop,
        _decision(decision="needs_more_work"),
        _todo(),
        "decision-1",
        TodoStatus.NEEDS_MORE_WORK,
    ) is TodoStatus.NEEDS_MORE_WORK


@pytest.mark.asyncio
async def test_managed_promotion_verifies_receipt_identity() -> None:
    """Promotion succeeds only after the returned receipt verifies exact ownership."""
    receipt = SimpleNamespace(verify_for=MagicMock())
    task_return = object()
    loop = SimpleNamespace(
        _task_return_repo=SimpleNamespace(get_by_id=AsyncMock(return_value=task_return)),
        _ensure_managed_self_improve_promotion=AsyncMock(return_value=receipt),
    )
    decision = _decision()
    todo = _todo()

    assert await subject._promote_managed_todo(loop, decision, todo, "/repo") is True
    receipt.verify_for.assert_called_once_with(
        todo_id="todo-1",
        project_id="project-1",
        repo_root="/repo",
        return_id="return-1",
    )


@pytest.mark.asyncio
async def test_managed_promotion_fails_closed_without_verified_return() -> None:
    """Missing repositories, returns, and invalid receipts all block promotion."""
    decision = _decision()
    todo = _todo()
    assert await subject._promote_managed_todo(
        SimpleNamespace(_task_return_repo=None), decision, todo, "/repo"
    ) is False
    assert await subject._promote_managed_todo(
        SimpleNamespace(
            _task_return_repo=SimpleNamespace(get_by_id=AsyncMock(return_value=None))
        ),
        decision,
        todo,
        "/repo",
    ) is False
    bad_receipt = SimpleNamespace(
        verify_for=MagicMock(side_effect=ValueError("wrong owner"))
    )
    loop = SimpleNamespace(
        _task_return_repo=SimpleNamespace(get_by_id=AsyncMock(return_value=object())),
        _ensure_managed_self_improve_promotion=AsyncMock(return_value=bad_receipt),
    )
    assert await subject._promote_managed_todo(
        loop, decision, todo, "/repo"
    ) is False


@pytest.mark.asyncio
async def test_commit_transition_handles_race_and_tracks_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lost CAS writes stay unapplied; successful completion tracks all side effects."""
    decision = _decision(decision="complete")
    todo = _todo()
    losing_loop = SimpleNamespace(
        _todo_repo=SimpleNamespace(
            transition=AsyncMock(side_effect=ConcurrencyError("stale"))
        )
    )
    assert await subject._commit_transition(
        losing_loop, decision, todo, "decision-1", TodoStatus.COMPLETE
    ) == (False, False)

    tasks: list[asyncio.Task[object]] = []
    audit = AsyncMock()
    monkeypatch.setattr(subject, "_record_audit_event", audit)
    loop = SimpleNamespace(
        _todo_repo=SimpleNamespace(transition=AsyncMock()),
        _ledger_add=MagicMock(),
        _applied_decisions=set(),
        _track_background_task=tasks.append,
        _auto_record_episode=AsyncMock(),
        _attempt_completed_push=AsyncMock(return_value=True),
        _ephemeral_account_manager=object(),
        _maybe_cleanup_ephemeral=AsyncMock(),
    )

    result = await subject._commit_transition(
        loop, decision, todo, "decision-1", TodoStatus.COMPLETE
    )
    await asyncio.gather(*tasks)

    assert result == (True, True)
    assert len(tasks) == 2
    loop._ledger_add.assert_called_once_with(loop._applied_decisions, "decision-1")
    audit.assert_awaited_once_with(loop, decision, todo, TodoStatus.COMPLETE)


@pytest.mark.asyncio
async def test_record_audit_event_is_best_effort() -> None:
    """Audit storage errors never roll back a completed state transition."""
    audit_repo = SimpleNamespace(record_typed=AsyncMock(side_effect=RuntimeError("offline")))
    loop = SimpleNamespace(_audit_repo=audit_repo)

    await subject._record_audit_event(
        loop, _decision(), _todo(), TodoStatus.NEEDS_MORE_WORK
    )

    audit_repo.record_typed.assert_awaited_once()
