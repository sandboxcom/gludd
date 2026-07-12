"""D2: run_project_gate is wired into the EVENT LOOP RECONCILE phase.

An external target project's quality gate (via its ``project.yml``) MUST gate
a COMPLETE decision during reconciliation, not just during review. The review
path (``apply_decision``) was wired in D2; this test covers the RECONCILE
phase (``_phase_reconcile_completed_decisions``) which processes batched
``TaskDecisionModel`` rows and must apply the same policy.

Wiring contract (all exercised below):
  * For a COMPLETE decision in the reconcile phase whose project has a
    ``project.yml``, ``run_project_gate`` is invoked with the resolved
    workspace/repo_root.
  * A FAILING project gate downgrades the transition to NEEDS_MORE_WORK.
  * A PASSING project gate lets the decision proceed to COMPLETE.
  * A decision whose project has NO ``project.yml`` skips the gate.
  * A decision with ``repo_root=None`` skips the gate (fail-safe).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
    return loop, {"session": session, "todo_repo": todo_repo}


def _decision(decision_id, todo_id, kind, project_id=None):
    d = MagicMock()
    d.id = decision_id
    d.return_id = f"RET-{decision_id}"
    d.matched_todo_id = todo_id
    d.decision = kind
    d.confidence = 0.95
    d.project_id = project_id
    d.evidence_refs = json.dumps([])
    d.audit_notes = json.dumps([])
    return d


def _reviewing_todo(todo_id="TODO-001", version=1, project_id=None, worktree="/tmp/wt"):
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.status = TodoStatus.REVIEWING_RETURN.value
    todo.version = version
    todo.project_id = project_id
    todo.worktree = worktree
    return todo


def _stub_decisions(session, decisions):
    result = MagicMock()
    result.scalars.return_value.all.return_value = decisions
    session.execute.return_value = result


def _gate_report(passed: bool) -> dict:
    return {
        "passed": passed,
        "overall": "PASS" if passed else "FAIL",
        "project": "demo",
        "checks": [
            {"name": "lint", "passed": True, "summary": "lint: PASS"},
            {"name": "test", "passed": passed, "summary": f"test: {'PASS' if passed else 'FAIL'}"},
        ],
        "missing": [],
        "passed_count": 2 if passed else 1,
        "failed_count": 0 if passed else 1,
    }


def _write_project_yml(root) -> None:
    from pathlib import Path
    _root = Path(root)
    _root.mkdir(parents=True, exist_ok=True)
    (_root / "project.yml").write_text(
        'name: demo\n'
        'allowed_exec: ["true"]\n'
        'commands:\n'
        '  lint: "true"\n'
        '  test: "true"\n',
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_gate_called_during_reconcile_with_project_yml(tmp_path):
    """When a project.yml exists, the reconcile phase invokes run_project_gate."""
    _write_project_yml(tmp_path)
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={
        "tick_interval": 1.0,
        "repo_root": str(tmp_path),
    })
    d = _decision("D-REC-1", "TODO-REC-1", "complete", project_id="PROJ-1")
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-REC-1", project_id="PROJ-1")
    )
    mocks["todo_repo"].transition = AsyncMock()

    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda dec, _tr, _rr, **_kw: dec,
    ), patch(
        "general_ludd.quality.project_gate.run_project_gate"
    ) as mock_gate:
        mock_gate.return_value = _gate_report(True)
        await loop._phase_reconcile_completed_decisions()

    mock_gate.assert_called_once()
    assert mock_gate.call_args.args[0] == str(tmp_path)


@pytest.mark.asyncio
async def test_failing_gate_downgrades_during_reconcile(tmp_path):
    """A FAILING project gate downgrades reconcile transition to NEEDS_MORE_WORK."""
    _write_project_yml(tmp_path)
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={
        "tick_interval": 1.0,
        "repo_root": str(tmp_path),
    })
    d = _decision("D-REC-2", "TODO-REC-2", "complete", project_id="PROJ-2")
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-REC-2", project_id="PROJ-2")
    )
    mocks["todo_repo"].transition = AsyncMock()

    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda dec, _tr, _rr, **_kw: dec,
    ), patch(
        "general_ludd.quality.project_gate.run_project_gate"
    ) as mock_gate:
        mock_gate.return_value = _gate_report(False)
        await loop._phase_reconcile_completed_decisions()

    mocks["todo_repo"].transition.assert_awaited_once()
    assert mocks["todo_repo"].transition.call_args.args[1] == TodoStatus.NEEDS_MORE_WORK


@pytest.mark.asyncio
async def test_passing_gate_allows_complete_during_reconcile(tmp_path):
    """A PASSING project gate lets the decision proceed to COMPLETE."""
    _write_project_yml(tmp_path)
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={
        "tick_interval": 1.0,
        "repo_root": str(tmp_path),
    })
    d = _decision("D-REC-3", "TODO-REC-3", "complete", project_id="PROJ-3")
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-REC-3", project_id="PROJ-3")
    )
    mocks["todo_repo"].transition = AsyncMock()

    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda dec, _tr, _rr, **_kw: dec,
    ), patch(
        "general_ludd.quality.project_gate.run_project_gate"
    ) as mock_gate:
        mock_gate.return_value = _gate_report(True)
        await loop._phase_reconcile_completed_decisions()

    mocks["todo_repo"].transition.assert_awaited_once()
    assert mocks["todo_repo"].transition.call_args.args[1] == TodoStatus.COMPLETE


@pytest.mark.asyncio
async def test_gate_skipped_when_no_project_yml(tmp_path):
    """No project.yml → gate is skipped, decision proceeds normally to COMPLETE."""
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={
        "tick_interval": 1.0,
        "repo_root": str(tmp_path),
    })
    d = _decision("D-REC-4", "TODO-REC-4", "complete", project_id="PROJ-4")
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-REC-4", project_id="PROJ-4")
    )
    mocks["todo_repo"].transition = AsyncMock()

    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda dec, _tr, _rr, **_kw: dec,
    ), patch(
        "general_ludd.quality.project_gate.run_project_gate"
    ) as mock_gate:
        await loop._phase_reconcile_completed_decisions()

    mock_gate.assert_not_called()
    mocks["todo_repo"].transition.assert_awaited_once()
    assert mocks["todo_repo"].transition.call_args.args[1] == TodoStatus.COMPLETE


@pytest.mark.asyncio
async def test_gate_skipped_when_repo_root_none():
    """repo_root=None → no workspace to gate → skip (fail-safe)."""
    loop, mocks = _make_loop(config={"tick_interval": 1.0})
    d = _decision("D-REC-5", "TODO-REC-5", "complete", project_id="PROJ-5")
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-REC-5", project_id="PROJ-5")
    )
    mocks["todo_repo"].transition = AsyncMock()

    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda dec, _tr, _rr, **_kw: dec,
    ), patch(
        "general_ludd.quality.project_gate.run_project_gate"
    ) as mock_gate:
        await loop._phase_reconcile_completed_decisions()

    mock_gate.assert_not_called()


@pytest.mark.asyncio
async def test_gate_not_run_for_non_complete_decisions(tmp_path):
    """A non-complete decision does NOT trigger the project gate in reconcile."""
    _write_project_yml(tmp_path)
    (tmp_path / "proof.txt").write_text("ok")
    loop, mocks = _make_loop(config={
        "tick_interval": 1.0,
        "repo_root": str(tmp_path),
    })
    d = _decision("D-REC-6", "TODO-REC-6", "needs_more_work", project_id="PROJ-6")
    _stub_decisions(mocks["session"], [d])
    mocks["todo_repo"].get_by_id = AsyncMock(
        return_value=_reviewing_todo("TODO-REC-6", project_id="PROJ-6")
    )
    mocks["todo_repo"].transition = AsyncMock()

    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda dec, _tr, _rr, **_kw: dec,
    ), patch(
        "general_ludd.quality.project_gate.run_project_gate"
    ) as mock_gate:
        await loop._phase_reconcile_completed_decisions()

    mock_gate.assert_not_called()
