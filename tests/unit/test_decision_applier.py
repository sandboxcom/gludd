"""Hermetic unit tests for general_ludd.review.decision_applier.apply_decision.

Covers the decision->todo-transition state machine, the completion-verifier
gate, child-todo creation, low-confidence validation creation, and the
atomic-failure semantics (a raised transition MUST NOT trigger downstream
side-effect creates).

The source module has no ``rollback()`` function and no ``approve``/``revert``
decision types. The deliverable's six categories are mapped to the ACTUAL
source behavior:
  1. "approve"        -> decision="complete" with verify_completion pass-through
  2. "revert"         -> decision="failed" (the status-reversing transition)
  3. "rollback()"     -> atomic failure: transition raises -> no downstream creates
  4. invalid review_id -> TaskDecision ValueError + matched-todo-not-found no-op
  5. already applied  -> source choice is ERROR (transition raises; not idempotent)
  6. mismatched SHA   -> verify_completion downgrades complete->needs_more_work

Side effects under test are ``todo_repo.transition`` and ``todo_repo.create``;
there are no git operations in this module. The "commit SHA" gate is
``verify_completion`` (invoked via ``asyncio.to_thread`` for complete decisions).
"""

from __future__ import annotations

import asyncio
import subprocess
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.db.repository import InvalidTransitionError
from general_ludd.review.decision_applier import apply_decision
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus

_SENTINEL: Any = object()


def _decision(
    decision: str = "complete",
    *,
    return_id: str = "RET-TEST-001",
    matched_todo_id: str | None = "TODO-TEST-001",
    confidence: float = 0.9,
    evidence_refs: list[str] | None = None,
    child_todos: list[dict[str, Any]] | None = None,
) -> TaskDecision:
    return TaskDecision(
        return_id=return_id,
        matched_todo_id=matched_todo_id,
        decision=decision,
        confidence=confidence,
        evidence_refs=evidence_refs or [],
        child_todos=child_todos or [],
    )


def _mock_todo(
    todo_id: str = "TODO-TEST-001",
    version: int = 3,
    project_id: str = "PROJ-1",
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
    """Run the sync verify_completion inline instead of in a worker thread."""

    async def _runner(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    with patch.object(asyncio, "to_thread", _runner):
        yield


@contextmanager
def _passthrough_verify() -> Any:
    """verify_completion returns the decision unchanged (gate always passes)."""
    with patch(
        "general_ludd.review.completion_verifier.verify_completion",
        new=lambda d, _tr, _rr, **_kw: d,
    ):
        yield


class TestApplyComplete:
    """Category 1: a verified 'complete' decision transitions the todo to COMPLETE."""

    async def test_complete_transitions_to_complete_status(self) -> None:
        decision = _decision("complete", evidence_refs=["artifact:x"])
        todo_repo, session = _make_repos()

        with _inline_to_thread(), _passthrough_verify():
            await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_awaited_once()
        call = todo_repo.transition.call_args
        assert call.args[0] == "TODO-TEST-001"
        assert call.args[1] == TodoStatus.COMPLETE
        assert call.args[2] == 3
        assert call.kwargs == {"project_id": "PROJ-1"}

    async def test_complete_forwards_repo_root_to_verifier(self) -> None:
        decision = _decision("complete", evidence_refs=["artifact:x"])
        todo_repo, session = _make_repos()

        with _inline_to_thread(), patch(
            "general_ludd.review.completion_verifier.verify_completion"
        ) as mock_verify:
            mock_verify.return_value = decision
            await apply_decision(decision, todo_repo, session, repo_root="/r")

        mock_verify.assert_called_once_with(decision, None, "/r")

    async def test_complete_uses_none_repo_root_by_default(self) -> None:
        decision = _decision("complete", evidence_refs=["artifact:x"])
        todo_repo, session = _make_repos()

        with _inline_to_thread(), patch(
            "general_ludd.review.completion_verifier.verify_completion"
        ) as mock_verify:
            mock_verify.return_value = decision
            await apply_decision(decision, todo_repo, session)

        mock_verify.assert_called_once_with(decision, None, None)


class TestApplyNonCompleteDecisions:
    """Category 2: non-complete decisions transition to their mapped status.

    The deliverable's 'revert' maps to 'failed' (the status-reversing transition)
    and the other non-terminal outcomes.
    """

    @pytest.mark.parametrize(
        ("decision_str", "expected_status"),
        [
            ("failed", TodoStatus.FAILED),
            ("needs_more_work", TodoStatus.NEEDS_MORE_WORK),
            ("blocked", TodoStatus.BLOCKED),
            ("manual_hold", TodoStatus.MANUAL_HOLD),
        ],
    )
    async def test_non_complete_transitions_to_mapped_status(
        self,
        decision_str: str,
        expected_status: TodoStatus,
    ) -> None:
        decision = _decision(decision_str)
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_awaited_once()
        assert todo_repo.transition.call_args.args[1] == expected_status

    async def test_non_complete_skips_completion_verifier(self) -> None:
        decision = _decision("failed")
        todo_repo, session = _make_repos()

        with patch(
            "general_ludd.review.completion_verifier.verify_completion"
        ) as mock_verify:
            await apply_decision(decision, todo_repo, session)

        mock_verify.assert_not_called()

    async def test_failed_transition_carries_version_and_project(self) -> None:
        decision = _decision("failed")
        todo_repo, session = _make_repos(_mock_todo(version=7, project_id="P2"))

        await apply_decision(decision, todo_repo, session)

        call = todo_repo.transition.call_args
        assert call.args[2] == 7
        assert call.kwargs == {"project_id": "P2"}


class TestAtomicFailure:
    """Category 3: there is no rollback() function. The atomicity guarantee is
    that a raised transition halts execution before any downstream create().
    A wrong decision-apply must NOT leave half-applied side effects behind."""

    async def test_transition_failure_blocks_child_creation(self) -> None:
        decision = _decision(
            "needs_more_work",
            child_todos=[{"title": "Sub-task", "description": "fix it"}],
        )
        todo_repo, session = _make_repos()
        todo_repo.transition = AsyncMock(side_effect=InvalidTransitionError("nope"))

        with pytest.raises(InvalidTransitionError):
            await apply_decision(decision, todo_repo, session)

        todo_repo.create.assert_not_called()

    async def test_transition_failure_blocks_validation_creation(self) -> None:
        decision = _decision("needs_more_work", confidence=0.1)
        todo_repo, session = _make_repos()
        todo_repo.transition = AsyncMock(side_effect=InvalidTransitionError("nope"))

        with pytest.raises(InvalidTransitionError):
            await apply_decision(decision, todo_repo, session)

        todo_repo.create.assert_not_called()

    async def test_successful_transition_then_children_created(self) -> None:
        decision = _decision(
            "needs_more_work",
            child_todos=[{"title": "A"}, {"title": "B"}],
        )
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        assert todo_repo.create.await_count == 2


class TestInvalidIdentifiers:
    """Category 4: invalid review_id -> ValueError; matched todo not found -> no-op."""

    def test_empty_return_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="return_id must not be empty"):
            TaskDecision(return_id="", decision="complete")

    def test_whitespace_return_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="return_id must not be empty"):
            TaskDecision(return_id="   ", decision="complete")

    async def test_matched_todo_not_found_is_silent_noop(self) -> None:
        decision = _decision("failed", matched_todo_id="TODO-GHOST")
        todo_repo, session = _make_repos(todo=None)

        await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_not_called()
        todo_repo.create.assert_not_called()

    async def test_none_matched_todo_id_skips_lookup(self) -> None:
        decision = _decision("failed", matched_todo_id=None)
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        todo_repo.get_by_id.assert_not_called()
        todo_repo.transition.assert_not_called()
        todo_repo.create.assert_not_called()


class TestAlreadyApplied:
    """Category 5: apply_decision has no internal idempotency check. Re-applying
    on a terminal state relies on the repository's InvalidTransitionError. The
    source's choice is ERROR (not silent idempotency) -- the error must propagate."""

    async def test_reapply_on_terminal_state_propagates_error(self) -> None:
        decision = _decision("complete", evidence_refs=["artifact:x"])
        todo_repo, session = _make_repos()
        todo_repo.transition = AsyncMock(
            side_effect=InvalidTransitionError("already complete")
        )

        with _inline_to_thread(), _passthrough_verify(), pytest.raises(InvalidTransitionError):
            await apply_decision(decision, todo_repo, session)

        todo_repo.create.assert_not_called()

    async def test_no_internal_dedup_two_calls_two_transitions(self) -> None:
        decision = _decision("failed")
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)
        await apply_decision(decision, todo_repo, session)

        assert todo_repo.transition.await_count == 2


class TestCommitShaSecurityGate:
    """Category 6: a mismatched / unsafe commit SHA must NOT transition to
    COMPLETE. verify_completion downgrades complete->needs_more_work, so
    apply_decision transitions to NEEDS_MORE_WORK instead. This is the
    security boundary that prevents applying the wrong commit."""

    async def test_unsafe_commit_sha_downgrades_before_apply(
        self, tmp_path: Any
    ) -> None:
        decision = _decision(
            "complete",
            evidence_refs=["commit:../../etc/passwd"],
        )
        todo_repo, session = _make_repos()

        with _inline_to_thread():
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        todo_repo.transition.assert_awaited_once()
        assert todo_repo.transition.call_args.args[1] == TodoStatus.NEEDS_MORE_WORK

    async def test_absent_commit_sha_downgrades_before_apply(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            result = MagicMock()
            result.returncode = 1
            return result

        monkeypatch.setattr(subprocess, "run", _fake_run)

        decision = _decision(
            "complete",
            evidence_refs=["commit:deadbeef1234"],
        )
        todo_repo, session = _make_repos()

        with _inline_to_thread():
            await apply_decision(
                decision, todo_repo, session, repo_root=str(tmp_path)
            )

        assert todo_repo.transition.call_args.args[1] == TodoStatus.NEEDS_MORE_WORK

    async def test_unresolved_repo_root_downgrades_before_apply(self) -> None:
        decision = _decision(
            "complete",
            evidence_refs=["commit:deadbeef1234"],
        )
        todo_repo, session = _make_repos()

        with _inline_to_thread():
            await apply_decision(decision, todo_repo, session, repo_root=None)

        assert todo_repo.transition.call_args.args[1] == TodoStatus.NEEDS_MORE_WORK


class TestIgnoreDuplicate:
    async def test_ignore_duplicate_is_silent_noop(self) -> None:
        decision = _decision("ignore_duplicate")
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        todo_repo.get_by_id.assert_not_called()
        todo_repo.transition.assert_not_called()
        todo_repo.create.assert_not_called()


class TestChildTodos:
    async def test_child_todos_created_with_backlog_status(self) -> None:
        children = [
            {"title": "Fix tests", "description": "broken"},
            {"title": "Update docs"},
        ]
        decision = _decision("needs_more_work", child_todos=children)
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        assert todo_repo.create.await_count == 2
        first = todo_repo.create.call_args_list[0].args[0]
        assert first["title"] == "Fix tests"
        assert first["description"] == "broken"
        assert first["parent_todo_id"] == "TODO-TEST-001"
        assert first["status"] == TodoStatus.BACKLOG
        assert first["work_type"] == "code"

    async def test_child_todo_defaults_when_fields_missing(self) -> None:
        decision = _decision("needs_more_work", child_todos=[{}])
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        payload = todo_repo.create.call_args.args[0]
        assert payload["title"] == "Child task"
        assert payload["description"] == ""


class TestLowConfidenceValidation:
    async def test_low_confidence_creates_validation_todo(self) -> None:
        decision = _decision("failed", confidence=0.2)
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        assert todo_repo.create.await_count == 1
        payload = todo_repo.create.call_args.args[0]
        assert "Validate return RET-TEST-001" in payload["title"]
        assert "0.2" in payload["description"]
        assert payload["status"] == TodoStatus.BACKLOG
        assert payload["work_type"] == "review"
        assert payload["parent_todo_id"] == "TODO-TEST-001"

    async def test_high_confidence_creates_no_validation_todo(self) -> None:
        decision = _decision("failed", confidence=0.9)
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        todo_repo.create.assert_not_called()

    async def test_boundary_confidence_exactly_threshold_no_validation(self) -> None:
        decision = _decision("failed", confidence=0.5)
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        todo_repo.create.assert_not_called()

    async def test_low_confidence_and_child_todos_both_created(self) -> None:
        decision = _decision(
            "failed",
            confidence=0.1,
            child_todos=[{"title": "X"}],
        )
        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        assert todo_repo.create.await_count == 2


class TestUnknownDecisionGuard:
    """Defensive guard at decision_applier.py:55-58. TaskDecision's own validator
    blocks unknown decision strings at construction, so this path is only
    reachable via post-construction mutation. The guard must still hold: no
    transition, no create."""

    async def test_mutated_unknown_decision_is_safe_noop(self) -> None:
        decision = _decision("failed")
        object.__setattr__(decision, "decision", "bogus_unknown")

        todo_repo, session = _make_repos()

        await apply_decision(decision, todo_repo, session)

        todo_repo.transition.assert_not_called()
        todo_repo.create.assert_not_called()
