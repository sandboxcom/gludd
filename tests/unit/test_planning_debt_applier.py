"""Structural tests for the debt applier."""

from __future__ import annotations

from typing import Any

from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.debt_applier import (
    DebtApplyResult,
    _build_addendum,
    _extract_todo_id,
    _fold_in,
)
from general_ludd.planning.debt_evaluator import DebtFinding


def _make_plan(**kwargs: Any) -> PlanArtifact:
    defaults: dict[str, Any] = {
        "todo_id": "test-todo-123",
        "title": "Test Plan",
        "description": "A test plan for unit tests",
        "target_files": ["src/foo.py", "tests/unit/test_foo.py"],
        "contracts": ["Bar must handle errors"],
        "notes": "",
    }
    defaults.update(kwargs)
    return PlanArtifact(**defaults)  # type: ignore[arg-type]


class TestExtractTodoId:
    def test_from_todo_id_attr(self) -> None:
        class _Obj:
            todo_id = "abc-123"

        assert _extract_todo_id(_Obj()) == "abc-123"

    def test_from_id_attr(self) -> None:
        class _Obj:
            id = 42

        assert _extract_todo_id(_Obj()) == "42"

    def test_from_dict_todo_id(self) -> None:
        assert _extract_todo_id({"todo_id": "xyz"}) == "xyz"

    def test_from_dict_id(self) -> None:
        assert _extract_todo_id({"id": "789"}) == "789"

    def test_none(self) -> None:
        assert _extract_todo_id(None) is None

    def test_empty_dict(self) -> None:
        assert _extract_todo_id({}) is None


class TestBuildAddendum:
    def test_empty(self) -> None:
        assert _build_addendum([]) == ""

    def test_single_gap(self) -> None:
        result = _build_addendum(["missing validation"])
        assert "Fold-in scope" in result
        assert "- missing validation" in result

    def test_multiple_gaps(self) -> None:
        result = _build_addendum(["gap A", "gap B"])
        assert "- gap A" in result
        assert "- gap B" in result


class TestFoldIn:
    def test_appends_gap_to_contracts(self) -> None:
        plan = _make_plan()
        finding = DebtFinding(gap="missing validation", kind="sharp_edge", touched_files=[])
        _fold_in(plan, finding)
        assert "missing validation" in plan.contracts

    def test_appends_gap_to_notes(self) -> None:
        plan = _make_plan(notes="existing note")
        finding = DebtFinding(gap="missing validation", kind="sharp_edge", touched_files=[])
        _fold_in(plan, finding)
        assert "Fold-in: missing validation" in plan.notes
        assert "existing note" in plan.notes

    def test_appends_gap_to_empty_notes(self) -> None:
        plan = _make_plan(notes="")
        finding = DebtFinding(gap="missing validation", kind="sharp_edge", touched_files=[])
        _fold_in(plan, finding)
        assert plan.notes == "Fold-in: missing validation"

    def test_merges_touched_files_deduped(self) -> None:
        plan = _make_plan(target_files=["src/a.py", "src/b.py"])
        finding = DebtFinding(gap="g", kind="sharp_edge", touched_files=["src/a.py", "src/c.py"])
        _fold_in(plan, finding)
        assert plan.target_files == ["src/a.py", "src/b.py", "src/c.py"]


class TestDebtApplyResult:
    def test_defaults(self) -> None:
        plan = _make_plan()
        result = DebtApplyResult(augmented_plan=plan)
        assert result.deferred_todo_ids == []
        assert result.folded_in == 0
        assert result.prompt_addendum == ""

    def test_with_values(self) -> None:
        plan = _make_plan()
        result = DebtApplyResult(
            augmented_plan=plan,
            deferred_todo_ids=["id1", "id2"],
            folded_in=3,
            prompt_addendum="### Fold-in scope\n\n- gap1",
        )
        assert len(result.deferred_todo_ids) == 2
        assert result.folded_in == 3
        assert "gap1" in result.prompt_addendum
