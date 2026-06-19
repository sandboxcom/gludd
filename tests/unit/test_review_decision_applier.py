"""Unit tests for general_ludd.review.decision_applier.

Covers all branches of apply_decision():
  - complete requires evidence_refs (ValueError guard)
  - ignore_duplicate short-circuits early
  - matched_todo_id is None short-circuits early
  - todo not found short-circuits early
  - unknown decision type short-circuits early
  - happy path for each mapped decision status
  - child_todos triggers create() per child
  - low-confidence (<0.5) triggers a validation-review child
  - high-confidence (>=0.5) does NOT create validation child
  - combined child_todos + low confidence → both creates
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import (
    AsyncMock,
    MagicMock,
)

import pytest

from general_ludd.review.decision_applier import _DECISION_STATUS_MAP, apply_decision
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_todo(todo_id: str = "TODO-AAA", version: int = 1, project_id: str | None = "proj-1") -> MagicMock:
    """Return a mock TodoModel with the attributes apply_decision reads."""
    todo = MagicMock()
    todo.todo_id = todo_id
    todo.version = version
    todo.project_id = project_id
    return todo


def _make_repo(todo: Any = None) -> AsyncMock:
    """Return an AsyncMock TodoRepository whose get_by_id returns *todo*."""
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=todo)
    repo.transition = AsyncMock()
    repo.create = AsyncMock()
    return repo


def _make_session() -> AsyncMock:
    return AsyncMock()


def _decision(**kwargs: Any) -> TaskDecision:
    """Convenience constructor — provide sensible defaults."""
    defaults: dict[str, Any] = {
        "return_id": "RET-001",
        "decision": "complete",
        "confidence": 0.9,
        "evidence_refs": ["ref-1"],
        "matched_todo_id": "TODO-AAA",
    }
    defaults.update(kwargs)
    return TaskDecision(**defaults)


# ---------------------------------------------------------------------------
# Group 1: guard / early-exit branches
# ---------------------------------------------------------------------------

class TestApplyDecisionGuards:
    @pytest.mark.asyncio
    async def test_complete_without_evidence_raises(self):
        """complete with no evidence_refs must raise ValueError immediately."""
        d = _decision(decision="complete", evidence_refs=[])
        repo = _make_repo()
        session = _make_session()

        with pytest.raises(ValueError, match="evidence_refs"):
            await apply_decision(d, repo, session)

        repo.get_by_id.assert_not_called()
        repo.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignore_duplicate_returns_early(self, caplog):
        """ignore_duplicate should log and return without touching the repo."""
        d = _decision(decision="ignore_duplicate", evidence_refs=[])
        repo = _make_repo()
        session = _make_session()

        with caplog.at_level(logging.INFO, logger="general_ludd.review.decision_applier"):
            await apply_decision(d, repo, session)

        repo.get_by_id.assert_not_called()
        repo.transition.assert_not_called()
        assert "RET-001" in caplog.text

    @pytest.mark.asyncio
    async def test_no_matched_todo_id_returns_early(self, caplog):
        """Decision with matched_todo_id=None should warn and return."""
        d = _decision(matched_todo_id=None, decision="needs_more_work", evidence_refs=[])
        repo = _make_repo()
        session = _make_session()

        with caplog.at_level(logging.WARNING, logger="general_ludd.review.decision_applier"):
            await apply_decision(d, repo, session)

        repo.get_by_id.assert_not_called()
        repo.transition.assert_not_called()
        assert "no matched_todo_id" in caplog.text.lower() or "nothing to apply" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_todo_not_found_returns_early(self, caplog):
        """When get_by_id returns None, log error and return without transition."""
        d = _decision(decision="failed", evidence_refs=[])
        repo = _make_repo(todo=None)
        session = _make_session()

        with caplog.at_level(logging.ERROR, logger="general_ludd.review.decision_applier"):
            await apply_decision(d, repo, session)

        repo.get_by_id.assert_awaited_once_with("TODO-AAA")
        repo.transition.assert_not_called()
        assert "TODO-AAA" in caplog.text

    @pytest.mark.asyncio
    async def test_unknown_decision_type_returns_early(self, caplog):
        """An unrecognised decision string should warn and skip transition.

        NOTE: TaskDecision validator rejects truly unknown decisions, so we
        patch _DECISION_STATUS_MAP to create the gap instead of the schema.
        """
        d = _decision(decision="needs_more_work", evidence_refs=[])
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        # Temporarily remove 'needs_more_work' from the map to simulate unknown.
        import general_ludd.review.decision_applier as _mod
        original_map = dict(_mod._DECISION_STATUS_MAP)
        _mod._DECISION_STATUS_MAP.pop("needs_more_work", None)
        try:
            with caplog.at_level(logging.WARNING, logger="general_ludd.review.decision_applier"):
                await apply_decision(d, repo, session)
        finally:
            _mod._DECISION_STATUS_MAP.update(original_map)

        repo.transition.assert_not_called()
        assert "needs_more_work" in caplog.text.lower() or "unknown" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Group 2: happy path for each mapped decision type
# ---------------------------------------------------------------------------

class TestApplyDecisionHappyPath:
    """Each decision in _DECISION_STATUS_MAP should call transition with the right status."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("decision_str,expected_status", sorted(
        _DECISION_STATUS_MAP.items()
    ))
    async def test_transition_called_for_each_decision(
        self, decision_str: str, expected_status: TodoStatus
    ):
        evidence = ["ev-1"] if decision_str == "complete" else []
        d = _decision(decision=decision_str, evidence_refs=evidence, confidence=0.8)
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.transition.assert_awaited_once_with(
            "TODO-AAA",
            expected_status,
            1,  # todo.version
            project_id="proj-1",
        )

    @pytest.mark.asyncio
    async def test_complete_with_evidence_succeeds(self):
        """complete + evidence_refs should not raise and should call transition."""
        d = _decision(decision="complete", evidence_refs=["ev-1"], confidence=0.9)
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.transition.assert_awaited_once()
        args = repo.transition.call_args
        assert args[0][1] == TodoStatus.COMPLETE


# ---------------------------------------------------------------------------
# Group 3: child todo creation
# ---------------------------------------------------------------------------

class TestApplyDecisionChildTodos:
    @pytest.mark.asyncio
    async def test_no_child_todos_skips_create(self):
        """When child_todos is empty, repo.create must not be called."""
        d = _decision(decision="needs_more_work", evidence_refs=[], confidence=0.9, child_todos=[])
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_child_todo_creates_one_row(self):
        """One entry in child_todos → one repo.create call."""
        child = {"title": "sub-task", "description": "do it"}
        d = _decision(
            decision="needs_more_work",
            evidence_refs=[],
            confidence=0.9,
            child_todos=[child],
        )
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.create.assert_awaited_once()
        payload = repo.create.call_args[0][0]
        assert payload["title"] == "sub-task"
        assert payload["description"] == "do it"
        assert payload["parent_todo_id"] == "TODO-AAA"
        assert payload["status"] == TodoStatus.BACKLOG

    @pytest.mark.asyncio
    async def test_multiple_child_todos_creates_multiple_rows(self):
        """Three children → three repo.create calls."""
        children = [
            {"title": "child A"},
            {"title": "child B", "description": "desc B"},
            {"title": "child C"},
        ]
        d = _decision(
            decision="needs_more_work",
            evidence_refs=[],
            confidence=0.9,
            child_todos=children,
        )
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        assert repo.create.await_count == 3
        titles = [c[0][0]["title"] for c in repo.create.call_args_list]
        assert sorted(titles) == sorted(["child A", "child B", "child C"])

    @pytest.mark.asyncio
    async def test_child_todo_uses_default_title_when_missing(self):
        """A child dict without 'title' should fall back to 'Child task'."""
        d = _decision(
            decision="needs_more_work",
            evidence_refs=[],
            confidence=0.9,
            child_todos=[{"description": "no title here"}],
        )
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        payload = repo.create.call_args[0][0]
        assert payload["title"] == "Child task"

    @pytest.mark.asyncio
    async def test_child_todo_default_empty_description(self):
        """A child dict without 'description' should produce empty string."""
        d = _decision(
            decision="needs_more_work",
            evidence_refs=[],
            confidence=0.9,
            child_todos=[{"title": "only title"}],
        )
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        payload = repo.create.call_args[0][0]
        assert payload["description"] == ""

    @pytest.mark.asyncio
    async def test_child_todo_work_type_is_code(self):
        """Child todos should always be work_type='code'."""
        d = _decision(
            decision="needs_more_work",
            evidence_refs=[],
            confidence=0.9,
            child_todos=[{"title": "impl"}],
        )
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        payload = repo.create.call_args[0][0]
        assert payload["work_type"] == "code"


# ---------------------------------------------------------------------------
# Group 4: low-confidence validation task
# ---------------------------------------------------------------------------

class TestApplyDecisionLowConfidence:
    @pytest.mark.asyncio
    async def test_low_confidence_creates_validation_task(self):
        """confidence < 0.5 must trigger creation of a review validation todo."""
        d = _decision(decision="needs_more_work", evidence_refs=[], confidence=0.3)
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.create.assert_awaited_once()
        payload = repo.create.call_args[0][0]
        assert "RET-001" in payload["title"]
        assert payload["work_type"] == "review"
        assert payload["parent_todo_id"] == "TODO-AAA"
        assert payload["status"] == TodoStatus.BACKLOG
        assert "0.3" in payload["description"]

    @pytest.mark.asyncio
    async def test_confidence_exactly_at_threshold_no_validation(self):
        """confidence == 0.5 is NOT below threshold; no validation task."""
        d = _decision(decision="needs_more_work", evidence_refs=[], confidence=0.5)
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_confidence_no_validation_task(self):
        """confidence >= 0.5 must NOT create a validation todo."""
        d = _decision(decision="needs_more_work", evidence_refs=[], confidence=0.9)
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_confidence_zero_creates_validation_task(self):
        """confidence == 0.0 is below threshold and must trigger validation."""
        d = _decision(decision="needs_more_work", evidence_refs=[], confidence=0.0)
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.create.assert_awaited_once()
        payload = repo.create.call_args[0][0]
        assert payload["work_type"] == "review"

    @pytest.mark.asyncio
    async def test_confidence_just_below_threshold(self):
        """confidence = 0.499 < 0.5 triggers validation."""
        d = _decision(decision="needs_more_work", evidence_refs=[], confidence=0.499)
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.create.assert_awaited_once()


# ---------------------------------------------------------------------------
# Group 5: combined scenarios
# ---------------------------------------------------------------------------

class TestApplyDecisionCombined:
    @pytest.mark.asyncio
    async def test_child_todos_and_low_confidence_both_create(self):
        """child_todos + low confidence → child create + validation create."""
        children = [{"title": "sub A"}, {"title": "sub B"}]
        d = _decision(
            decision="blocked",
            evidence_refs=[],
            confidence=0.1,
            child_todos=children,
        )
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        # 2 child creates + 1 validation create = 3 total
        assert repo.create.await_count == 3
        work_types = [c[0][0]["work_type"] for c in repo.create.call_args_list]
        assert sorted(work_types) == sorted(["code", "code", "review"])

    @pytest.mark.asyncio
    async def test_complete_with_children_and_high_confidence(self):
        """complete + children + high confidence → transition + child creates, no validation."""
        children = [{"title": "follow-up"}]
        d = _decision(
            decision="complete",
            evidence_refs=["proof-1"],
            confidence=0.95,
            child_todos=children,
        )
        todo = _make_todo()
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.transition.assert_awaited_once()
        assert repo.create.await_count == 1
        payload = repo.create.call_args[0][0]
        assert payload["work_type"] == "code"

    @pytest.mark.asyncio
    async def test_project_id_propagated_to_transition(self):
        """The todo's project_id should be forwarded as kwarg to transition."""
        d = _decision(decision="manual_hold", evidence_refs=[], confidence=0.9)
        todo = _make_todo(project_id="proj-XYZ")
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        _args, _kwargs = repo.transition.call_args
        assert _kwargs.get("project_id") == "proj-XYZ"

    @pytest.mark.asyncio
    async def test_project_id_none_propagated(self):
        """project_id=None on todo is also forwarded as-is."""
        d = _decision(decision="failed", evidence_refs=[], confidence=0.9)
        todo = _make_todo(project_id=None)
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        _args, _kwargs = repo.transition.call_args
        assert _kwargs.get("project_id") is None

    @pytest.mark.asyncio
    async def test_transition_receives_correct_version(self):
        """transition should receive the version from the fetched todo object."""
        d = _decision(decision="blocked", evidence_refs=[], confidence=0.9)
        todo = _make_todo(version=7)
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        args = repo.transition.call_args[0]
        assert args[2] == 7  # expected_version

    @pytest.mark.asyncio
    async def test_get_by_id_called_with_matched_todo_id(self):
        """get_by_id must be called with exactly the matched_todo_id."""
        d = _decision(decision="needs_more_work", evidence_refs=[], confidence=0.9,
                      matched_todo_id="TODO-XYZ99")
        todo = _make_todo(todo_id="TODO-XYZ99")
        repo = _make_repo(todo=todo)
        session = _make_session()

        await apply_decision(d, repo, session)

        repo.get_by_id.assert_awaited_once_with("TODO-XYZ99")


# ---------------------------------------------------------------------------
# Group 6: _DECISION_STATUS_MAP content check
# ---------------------------------------------------------------------------

class TestDecisionStatusMap:
    def test_all_statuses_map_to_todo_status_members(self):
        """Every value in _DECISION_STATUS_MAP must be a valid TodoStatus member."""
        for decision_str, status in _DECISION_STATUS_MAP.items():
            assert isinstance(status, TodoStatus), (
                f"{decision_str!r} maps to {status!r}, not a TodoStatus"
            )

    def test_map_covers_expected_decisions(self):
        """The map must cover the five non-ignore decisions."""
        expected = sorted(["complete", "needs_more_work", "failed", "blocked", "manual_hold"])
        actual = sorted(_DECISION_STATUS_MAP.keys())
        assert actual == expected

    def test_complete_maps_to_complete_status(self):
        assert _DECISION_STATUS_MAP["complete"] == TodoStatus.COMPLETE

    def test_failed_maps_to_failed_status(self):
        assert _DECISION_STATUS_MAP["failed"] == TodoStatus.FAILED

    def test_blocked_maps_to_blocked_status(self):
        assert _DECISION_STATUS_MAP["blocked"] == TodoStatus.BLOCKED

    def test_manual_hold_maps_to_manual_hold_status(self):
        assert _DECISION_STATUS_MAP["manual_hold"] == TodoStatus.MANUAL_HOLD

    def test_needs_more_work_maps_to_needs_more_work_status(self):
        assert _DECISION_STATUS_MAP["needs_more_work"] == TodoStatus.NEEDS_MORE_WORK
