"""Regression tests for event-loop reconcile defects (task #55).

Each test reproduces one reconcile defect in
``EventLoop._phase_reconcile_completed_decisions`` and asserts the fixed,
correct behaviour:

- F1: non-idempotent decision re-apply  -> re-running a tick must NOT apply the
      same decision twice (idempotent by decision id).
- F2: completed-work push lost/double   -> a completed todo's work is pushed
      EXACTLY once across repeated ticks (deduped by work id).
- F3: commit-swallow split-brain        -> a push/commit failure is surfaced
      (not silently swallowed) and the work is NOT marked pushed so it retries.
- F6: reconcile version race            -> a stale reconcile that loses the
      compare-and-set must skip rather than overwrite the newer state.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.repository import ConcurrencyError
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import TodoStatus


def _make_loop(**overrides):
    session = MagicMock(spec=AsyncSession)
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        config={"tick_interval": 1.0},
        session=session,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
        audit_repo=None,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "todo_repo": todo_repo,
    }


def _decision(decision_id, todo_id, kind, evidence_refs=None, audit_notes=None):
    d = MagicMock()
    d.id = decision_id
    d.return_id = f"RET-{decision_id}"
    d.matched_todo_id = todo_id
    d.decision = kind
    d.confidence = 0.95
    d.project_id = None
    # The reconcile phase's Layer-2 gate parses these as JSON for `complete`
    # decisions (json.loads); a bare MagicMock would raise TypeError and the row
    # would be skipped. Always stamp real JSON strings so the gate runs. A
    # `complete` decision needs a VERIFIABLE ref (+ a resolved repo_root in the
    # loop config) or it is correctly downgraded — callers that want the COMPLETE
    # transition to fire pass a satisfiable evidence_refs.
    d.evidence_refs = json.dumps(evidence_refs if evidence_refs is not None else [])
    d.audit_notes = json.dumps(audit_notes if audit_notes is not None else [])
    return d


def _reviewing_todo(todo_id="TODO-001", version=1, **extra):
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.status = TodoStatus.REVIEWING_RETURN.value
    todo.version = version
    todo.project_id = None
    for k, v in extra.items():
        setattr(todo, k, v)
    return todo


def _stub_decisions(session, decisions):
    result = MagicMock()
    result.scalars.return_value.all.return_value = decisions
    session.execute.return_value = result


@pytest.mark.asyncio
async def test_f1_decision_reapply_is_idempotent():
    """F1: the same decision applied twice across two ticks is a no-op the 2nd time.

    The DB-level status guard does not help here: the todo is read as
    REVIEWING_RETURN on BOTH ticks (e.g. the in-memory copy didn't refresh).
    Without an applied-decision ledger the transition fires twice.
    """
    loop, mocks = _make_loop()
    d = _decision("D1", "TODO-001", "needs_more_work")
    _stub_decisions(mocks["session"], [d])

    # get_by_id always returns a still-REVIEWING todo (stale copy), so the only
    # thing that can stop a double-apply is per-decision idempotency.
    mocks["todo_repo"].get_by_id = AsyncMock(return_value=_reviewing_todo())
    mocks["todo_repo"].transition = AsyncMock()

    await loop._phase_reconcile_completed_decisions()
    await loop._phase_reconcile_completed_decisions()

    assert mocks["todo_repo"].transition.await_count == 1, (
        "decision re-applied on the second tick — not idempotent (F1)"
    )


@pytest.mark.asyncio
async def test_f2_completed_work_pushed_exactly_once(tmp_path):
    """F2: completed work is pushed exactly once across repeated ticks."""
    # The Layer-2 gate must let the `complete` decision through so the push path
    # is reached: supply a resolved repo_root + a real, verifiable artifact ref.
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={"tick_interval": 1.0, "repo_root": str(tmp_path)})
    d = _decision("D2", "TODO-002", "complete", evidence_refs=["artifact:proof.txt"])
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-002", worktree="/tmp/wt", title="t")
    )
    mocks["todo_repo"].transition = AsyncMock()

    push_calls = []
    loop._try_commit_completed_work = AsyncMock(
        side_effect=lambda todo: push_calls.append(todo.todo_id)
    )

    await loop._phase_reconcile_completed_decisions()
    await loop._phase_reconcile_completed_decisions()

    assert push_calls == ["TODO-002"], (
        f"expected exactly-once push, got {push_calls} (F2 lost/double push)"
    )


@pytest.mark.asyncio
async def test_f3_push_failure_is_surfaced_not_swallowed(tmp_path):
    """F3: a commit/push failure must NOT silently leave COMPLETE-but-unpushed.

    The failure is surfaced (push_failures metric > 0) and the work is left
    UNMARKED so a later tick retries it — no silent split-brain.
    """
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={"tick_interval": 1.0, "repo_root": str(tmp_path)})
    d = _decision("D3", "TODO-003", "complete", evidence_refs=["artifact:proof.txt"])
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-003", worktree="/tmp/wt", title="t")
    )
    mocks["todo_repo"].transition = AsyncMock()

    attempts = {"n": 0}

    async def _failing_push(todo):
        attempts["n"] += 1
        raise RuntimeError("git push exploded")

    loop._try_commit_completed_work = _failing_push

    # Must not raise out of the phase, but must record the failure.
    await loop._phase_reconcile_completed_decisions()
    assert loop._tick_metrics.get("push_failures", 0) >= 1, (
        "push failure was swallowed silently (F3 split-brain)"
    )
    assert "TODO-003" not in loop._pushed_work, (
        "failed push was marked as done — would never retry (F3)"
    )

    # D10 backoff means the retry occurs on the next eligible tick, not
    # necessarily the immediate next call. Advance to the deterministic slot.
    loop._total_ticks = (-abs(hash("TODO-003"))) % 2
    await loop._phase_reconcile_completed_decisions()
    assert attempts["n"] == 2, "failed push was not retried on the next eligible tick (F3)"


@pytest.mark.asyncio
async def test_f6_stale_reconcile_loses_version_race(tmp_path):
    """F6: a reconcile that loses the compare-and-set must skip, not overwrite.

    The repository's guarded transition raises ConcurrencyError when a
    concurrent writer already moved the row. A stale reconcile must treat that
    as a lost race: skip silently, do NOT mark the decision applied, do NOT push.
    """
    # Verifiable evidence + resolved repo_root so the gate keeps the decision
    # `complete` and the code actually REACHES transition() (which then raises the
    # ConcurrencyError under test) — otherwise the row is skipped at the gate and
    # the race path is never exercised (a false pass).
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={"tick_interval": 1.0, "repo_root": str(tmp_path)})
    d = _decision("D6", "TODO-006", "complete", evidence_refs=["artifact:proof.txt"])
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-006", version=1, worktree="/tmp/wt", title="t")
    )
    mocks["todo_repo"].transition = AsyncMock(
        side_effect=ConcurrencyError("Version mismatch: expected 1, actual 2")
    )

    pushed = []
    loop._try_commit_completed_work = AsyncMock(
        side_effect=lambda todo: pushed.append(todo.todo_id)
    )

    # Must swallow the lost race (no exception bubbles out of the phase).
    await loop._phase_reconcile_completed_decisions()

    assert pushed == [], "stale reconcile pushed work despite losing the race (F6)"
    assert loop._tick_metrics.get("decisions_applied", 0) == 0, (
        "stale reconcile counted as applied (F6)"
    )
    # The decision must NOT be marked applied, so once the row settles a fresh
    # reconcile can legitimately apply it.
    assert loop._decision_id(d) not in loop._applied_decisions
