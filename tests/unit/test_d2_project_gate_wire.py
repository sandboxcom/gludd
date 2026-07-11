"""D2: run_project_gate is wired into the review/decision path.

An external target project's declared lint/typecheck/test gate (via its
``project.yml``) MUST gate a COMPLETE decision. Before D2, ``run_project_gate``
had zero production callers, so an external project's toolchain failures never
blocked a merge decision.

Wiring contract (all exercised below):
  * For a COMPLETE decision whose project has a ``project.yml``, the project
    gate is invoked with the resolved workspace/repo_root.
  * A FAILING project gate downgrades the decision (COMPLETE -> NEEDS_MORE_WORK)
    so the todo does not transition to COMPLETE on a broken external toolchain.
  * A PASSING project gate lets the decision proceed to COMPLETE.
  * A todo whose project has NO ``project.yml`` skips the gate entirely
    (no marker-file fallback here — the gate is opt-in via project.yml).
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from general_ludd.review.decision_applier import apply_decision
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus

_SENTINEL: Any = object()


def _decision(
    decision: str = "complete",
    *,
    return_id: str = "RET-D2-001",
    matched_todo_id: str | None = "TODO-D2-001",
    confidence: float = 0.9,
    evidence_refs: list[str] | None = None,
) -> TaskDecision:
    return TaskDecision(
        return_id=return_id,
        matched_todo_id=matched_todo_id,
        decision=decision,
        confidence=confidence,
        evidence_refs=evidence_refs or ["artifact:x"],
    )


def _mock_todo(
    todo_id: str = "TODO-D2-001",
    version: int = 3,
    project_id: str = "PROJ-D2",
) -> MagicMock:
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.version = version
    todo.project_id = project_id
    return todo


def _make_repos(todo: Any = _SENTINEL) -> tuple[AsyncMock, AsyncMock]:
    todo_repo = AsyncMock()
    resolved = _mock_todo() if todo is _SENTINEL else todo
    todo_repo.get_by_id = AsyncMock(return_value=resolved)
    todo_repo.transition = AsyncMock()
    todo_repo.create = AsyncMock()
    session = AsyncMock()
    return todo_repo, session


@contextmanager
def _inline_to_thread() -> Any:
    """Run the sync verify_completion / run_project_gate inline."""

    async def _runner(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    with patch.object(asyncio, "to_thread", _runner):
        yield


@contextmanager
def _passthrough_verify() -> Any:
    """verify_completion returns the decision unchanged (evidence gate passes)."""
    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda d, _tr, _rr, **_kw: d,
    ):
        yield


def _write_project_yml(root: Path) -> None:
    (root / "project.yml").write_text(
        'name: demo\n'
        'allowed_exec: ["true"]\n'
        'commands:\n'
        '  lint: "true"\n'
        '  test: "true"\n',
        encoding="utf-8",
    )


def _gate_report(passed: bool) -> dict[str, object]:
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


class TestProjectGateWiredIntoReview:
    async def test_run_project_gate_called_during_review(self, tmp_path: Path) -> None:
        _write_project_yml(tmp_path)
        decision = _decision("complete")
        todo_repo, session = _make_repos()

        with _inline_to_thread(), _passthrough_verify(), patch(
            "general_ludd.quality.project_gate.run_project_gate"
        ) as mock_gate:
            mock_gate.return_value = _gate_report(True)
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        mock_gate.assert_called_once()
        # The gate is invoked with the resolved workspace/repo_root.
        assert mock_gate.call_args.args[0] == str(tmp_path)

    async def test_run_project_gate_blocks_failing_project(
        self, tmp_path: Path
    ) -> None:
        _write_project_yml(tmp_path)
        decision = _decision("complete")
        todo_repo, session = _make_repos()

        with _inline_to_thread(), _passthrough_verify(), patch(
            "general_ludd.quality.project_gate.run_project_gate"
        ) as mock_gate:
            mock_gate.return_value = _gate_report(False)
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        # A failing external gate must NOT let the todo reach COMPLETE.
        todo_repo.transition.assert_awaited_once()
        assert todo_repo.transition.call_args.args[1] == TodoStatus.NEEDS_MORE_WORK

    async def test_run_project_gate_allows_passing_project(
        self, tmp_path: Path
    ) -> None:
        _write_project_yml(tmp_path)
        decision = _decision("complete")
        todo_repo, session = _make_repos()

        with _inline_to_thread(), _passthrough_verify(), patch(
            "general_ludd.quality.project_gate.run_project_gate"
        ) as mock_gate:
            mock_gate.return_value = _gate_report(True)
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        # A passing gate lets the decision proceed to COMPLETE.
        todo_repo.transition.assert_awaited_once()
        assert todo_repo.transition.call_args.args[1] == TodoStatus.COMPLETE

    async def test_run_project_gate_skipped_for_non_project_todos(
        self, tmp_path: Path
    ) -> None:
        # No project.yml written -> gate is skipped entirely.
        decision = _decision("complete")
        todo_repo, session = _make_repos()

        with _inline_to_thread(), _passthrough_verify(), patch(
            "general_ludd.quality.project_gate.run_project_gate"
        ) as mock_gate:
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        mock_gate.assert_not_called()
        # The decision still proceeds normally to COMPLETE.
        todo_repo.transition.assert_awaited_once()
        assert todo_repo.transition.call_args.args[1] == TodoStatus.COMPLETE

    async def test_gate_skipped_when_repo_root_unresolved(self) -> None:
        # repo_root=None -> no workspace to gate -> skip (fail-safe, no crash).
        decision = _decision("complete")
        todo_repo, session = _make_repos()

        with _inline_to_thread(), _passthrough_verify(), patch(
            "general_ludd.quality.project_gate.run_project_gate"
        ) as mock_gate:
            await apply_decision(decision, todo_repo, session, repo_root=None)

        mock_gate.assert_not_called()

    async def test_gate_not_run_for_non_complete_decisions(
        self, tmp_path: Path
    ) -> None:
        _write_project_yml(tmp_path)
        decision = _decision("needs_more_work")
        todo_repo, session = _make_repos()

        with _inline_to_thread(), patch(
            "general_ludd.quality.project_gate.run_project_gate"
        ) as mock_gate:
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        mock_gate.assert_not_called()

    async def test_failing_gate_records_per_check_results(
        self, tmp_path: Path
    ) -> None:
        _write_project_yml(tmp_path)
        decision = _decision("complete")
        todo_repo, session = _make_repos()

        captured: dict[str, object] = {}

        async def _capture_transition(*args: Any, **kwargs: Any) -> None:
            captured["status"] = args[1]

        todo_repo.transition = AsyncMock(side_effect=_capture_transition)

        with _inline_to_thread(), _passthrough_verify(), patch(
            "general_ludd.quality.project_gate.run_project_gate"
        ) as mock_gate:
            mock_gate.return_value = _gate_report(False)
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        # A failing gate creates a follow-up todo carrying the gate report so
        # the per-check results are observable downstream.
        assert captured["status"] == TodoStatus.NEEDS_MORE_WORK
        assert todo_repo.create.await_count >= 1
        payloads = [c.args[0] for c in todo_repo.create.call_args_list]
        joined = " ".join(str(p.get("description", "")) for p in payloads)
        assert "test: FAIL" in joined or "FAIL" in joined
