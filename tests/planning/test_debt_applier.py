"""Tests for the plan-time technical-debt decision applier (SLICE 2).

The applier turns :class:`DebtFindings` into two concrete effects:

* ``defer`` findings  -> a BACKLOG child todo per finding (via ``todo_repo.create``)
* ``fold_in`` findings -> an augmented :class:`PlanArtifact` (extended contracts,
  notes, target_files) plus a natural-language ``prompt_addendum``.

The fake ``todo_repo`` records every payload it is asked to create so the tests
can assert the exact mirrored shape without a database.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.debt_applier import (
    DebtApplyResult,
    apply_debt_findings,
)
from general_ludd.planning.debt_evaluator import DebtFinding, DebtFindings
from general_ludd.schemas.todo import TodoStatus


class _FakeTodo:
    def __init__(self, todo_id: str) -> None:
        self.todo_id = todo_id


class _FakeCreatedTodo:
    """Mimics ``TodoModel`` — create() returns an object carrying .todo_id."""

    def __init__(self, todo_id: str) -> None:
        self.todo_id = todo_id


class _FakeTodoRepo:
    """In-memory async todo repo: records payloads, returns a created object."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self._counter = 0

    async def create(self, todo_data: dict[str, Any]) -> _FakeCreatedTodo:
        self.created.append(dict(todo_data))
        self._counter += 1
        return _FakeCreatedTodo(f"TODO-DEBT-{self._counter}")


class _RaisingOnceTodoRepo(_FakeTodoRepo):
    """Raises on the FIRST create, then behaves normally (non-fatal test)."""

    async def create(self, todo_data: dict[str, Any]) -> _FakeCreatedTodo:
        if not self.created and self._counter == 0:
            # Mark that we attempted-and-failed so the second call succeeds.
            self._counter = 1
            raise RuntimeError("boom: simulated create failure")
        return await super().create(todo_data)


def _plan() -> PlanArtifact:
    return PlanArtifact(
        todo_id="TODO-PARENT",
        title="Add retry to fetcher",
        description="Wire retry into the fetch path",
        target_files=["src/pkg/fetch.py"],
        contracts=["fetch() retries on timeout"],
        notes="original note",
    )


def _defer_finding(gap: str = "add a caching layer for fetch results") -> DebtFinding:
    return DebtFinding(
        gap=gap,
        kind="missing_feature",
        why_it_matters="repeated fetches are wasteful",
        recommendation="defer",
        feature_creep_rationale="new capability beyond this task",
        effort="large",
        touched_files=["src/pkg/cache.py"],
    )


def _fold_in_finding(
    gap: str = "handle None response before parse",
) -> DebtFinding:
    return DebtFinding(
        gap=gap,
        kind="sharp_edge",
        why_it_matters="a None response would crash parse()",
        recommendation="fold_in",
        feature_creep_rationale="in-scope sharp edge",
        effort="small",
        touched_files=["src/pkg/fetch.py", "tests/pkg/test_fetch.py"],
    )


@pytest.mark.asyncio
async def test_defer_creates_one_backlog_todo() -> None:
    repo = _FakeTodoRepo()
    todo = _FakeTodo("TODO-PARENT")
    findings = DebtFindings(findings=[_defer_finding()])

    result = await apply_debt_findings(
        findings, _plan(), todo, repo, project_id="proj-1"
    )

    assert isinstance(result, DebtApplyResult)
    assert result.folded_in == 0
    assert len(repo.created) == 1
    payload = repo.created[0]
    assert payload["status"] == TodoStatus.BACKLOG
    assert payload["work_type"] == "code"
    assert payload["created_by"] == "debt_evaluator"
    assert payload["parent_todo_id"] == "TODO-PARENT"
    assert payload["project_id"] == "proj-1"
    # tags is serialised to a JSON string for the Text column (see debt_applier).
    assert json.loads(payload["tags"]) == ["tech-debt"]
    assert payload["title"].startswith("[tech-debt]")
    assert "repeated fetches are wasteful" in payload["description"]
    assert result.deferred_todo_ids == ["TODO-DEBT-1"]
    # A pure-defer batch leaves the plan a faithful copy.
    assert result.augmented_plan.contracts == _plan().contracts
    assert result.prompt_addendum == ""


@pytest.mark.asyncio
async def test_fold_in_augments_plan_without_creating_todo() -> None:
    repo = _FakeTodoRepo()
    todo = _FakeTodo("TODO-PARENT")
    finding = _fold_in_finding()
    findings = DebtFindings(findings=[finding])

    result = await apply_debt_findings(
        findings, _plan(), todo, repo, project_id="proj-1"
    )

    assert repo.created == []  # no todo for fold_in
    assert result.deferred_todo_ids == []
    assert result.folded_in == 1

    aug = result.augmented_plan
    # gap appended to contracts
    assert finding.gap in aug.contracts
    assert "fetch() retries on timeout" in aug.contracts  # original preserved
    # note line appended
    assert "original note" in aug.notes
    assert finding.gap in aug.notes
    # in-scope touched_files merged, deduped
    assert "tests/pkg/test_fetch.py" in aug.target_files
    assert aug.target_files.count("src/pkg/fetch.py") == 1  # dedup, already present
    # addendum names the gap
    assert result.prompt_addendum != ""
    assert "Fold-in scope" in result.prompt_addendum
    assert finding.gap in result.prompt_addendum


@pytest.mark.asyncio
async def test_mixed_defer_and_fold_in() -> None:
    repo = _FakeTodoRepo()
    todo = _FakeTodo("TODO-PARENT")
    findings = DebtFindings(
        findings=[_defer_finding(), _fold_in_finding()]
    )

    result = await apply_debt_findings(
        findings, _plan(), todo, repo, project_id="proj-1"
    )

    assert len(repo.created) == 1
    assert len(result.deferred_todo_ids) == 1
    assert result.folded_in == 1
    assert "handle None response before parse" in result.augmented_plan.contracts
    assert result.prompt_addendum != ""


@pytest.mark.asyncio
async def test_create_failure_is_non_fatal() -> None:
    repo = _RaisingOnceTodoRepo()
    todo = _FakeTodo("TODO-PARENT")
    findings = DebtFindings(
        findings=[
            _defer_finding("first defer that will fail"),
            _defer_finding("second defer that succeeds"),
        ]
    )

    result = await apply_debt_findings(
        findings, _plan(), todo, repo, project_id="proj-1"
    )

    # First create raised; the second still ran and is recorded.
    assert len(repo.created) == 1
    assert len(result.deferred_todo_ids) == 1
    assert isinstance(result, DebtApplyResult)


@pytest.mark.asyncio
async def test_empty_findings_is_noop() -> None:
    repo = _FakeTodoRepo()
    todo = _FakeTodo("TODO-PARENT")
    findings = DebtFindings(findings=[])

    result = await apply_debt_findings(
        findings, _plan(), todo, repo, project_id="proj-1"
    )

    assert repo.created == []
    assert result.deferred_todo_ids == []
    assert result.folded_in == 0
    assert result.prompt_addendum == ""
    # plan unchanged (faithful copy)
    assert result.augmented_plan.contracts == _plan().contracts
    assert result.augmented_plan.target_files == _plan().target_files
    assert result.augmented_plan.notes == _plan().notes
