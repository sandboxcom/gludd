"""Structural tests for the debt evaluator."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.debt_evaluator import (
    _DEFER_RATIONALE,
    _FOLD_IN_RATIONALE,
    DebtEvaluator,
    DebtFinding,
    DebtFindings,
    _basename,
    _is_impl_py,
    _is_test_file,
    _stem,
    _test_name_of,
    make_debt_evaluate_fn,
)


def _make_plan(**kwargs: Any) -> PlanArtifact:
    defaults: dict[str, Any] = {
        "todo_id": "test-todo-123",
        "title": "Add feature X",
        "description": "Implement feature X",
        "target_files": ["src/foo.py", "tests/unit/test_foo.py"],
        "contracts": ["Handle errors"],
        "notes": "",
    }
    defaults.update(kwargs)
    return PlanArtifact(**defaults)  # type: ignore[arg-type]


class TestHelpers:
    def test_basename(self) -> None:
        assert _basename("src/foo/bar.py") == "bar.py"
        assert _basename("bar.py") == "bar.py"

    def test_is_test_file(self) -> None:
        assert _is_test_file("tests/unit/test_foo.py") is True
        assert _is_test_file("src/foo.py") is False
        assert _is_test_file("test_helpers.py") is True

    def test_is_impl_py(self) -> None:
        assert _is_impl_py("src/foo.py") is True
        assert _is_impl_py("tests/unit/test_foo.py") is False
        assert _is_impl_py("README.md") is False

    def test_stem(self) -> None:
        assert _stem("src/foo.py") == "foo"

    def test_stem_non_py(self) -> None:
        assert _stem("README.md") == "README.md"

    def test_test_name_of(self) -> None:
        assert _test_name_of("tests/unit/test_foo.py") == "foo"
        assert _test_name_of("tests/test_bar.py") == "bar"


class TestDebtFinding:
    def test_defaults(self) -> None:
        f = DebtFinding(gap="test gap", kind="sharp_edge")
        assert f.recommendation == "defer"
        assert f.effort == "medium"
        assert f.touched_files == []
        assert f.feature_creep_rationale == ""
        assert f.why_it_matters == ""

    def test_override(self) -> None:
        f = DebtFinding(
            gap="g", kind="missing_feature",
            recommendation="fold_in", effort="small",
            touched_files=["a.py"], why_it_matters="important",
            feature_creep_rationale="in scope",
        )
        assert f.recommendation == "fold_in"
        assert f.effort == "small"
        assert f.touched_files == ["a.py"]
        assert f.why_it_matters == "important"


class TestDebtFindings:
    def test_defaults(self) -> None:
        df = DebtFindings()
        assert df.findings == []

    def test_with_findings(self) -> None:
        f = DebtFinding(gap="g", kind="sharp_edge")
        df = DebtFindings(findings=[f])
        assert len(df.findings) == 1


class TestClassify:
    def test_small_sharp_edge_in_scope_fold_in(self) -> None:
        evaluator = DebtEvaluator()
        plan = _make_plan(target_files=["src/foo.py"])
        finding = DebtFinding(
            gap="add retry", kind="sharp_edge", effort="small",
            touched_files=["src/foo.py"],
        )
        result = evaluator._classify(finding, plan, "Add feature X")
        assert result.recommendation == "fold_in"
        assert result.feature_creep_rationale == _FOLD_IN_RATIONALE

    def test_missing_feature_defers(self) -> None:
        evaluator = DebtEvaluator()
        plan = _make_plan(target_files=["src/foo.py"])
        finding = DebtFinding(
            gap="add new endpoint", kind="missing_feature", effort="small",
            touched_files=["src/foo.py"],
        )
        result = evaluator._classify(finding, plan, "Add feature X")
        assert result.recommendation == "defer"

    def test_large_effort_defers(self) -> None:
        evaluator = DebtEvaluator()
        plan = _make_plan(target_files=["src/foo.py"])
        finding = DebtFinding(
            gap="g", kind="sharp_edge", effort="large",
            touched_files=["src/foo.py"],
        )
        result = evaluator._classify(finding, plan, "Add feature X")
        assert result.recommendation == "defer"

    def test_zero_touched_files_defers(self) -> None:
        evaluator = DebtEvaluator()
        plan = _make_plan(target_files=["src/foo.py"])
        finding = DebtFinding(
            gap="g", kind="sharp_edge", effort="small",
            touched_files=[],
        )
        result = evaluator._classify(finding, plan, "Add feature X")
        assert result.recommendation == "defer"

    def test_touched_files_not_in_target_defers(self) -> None:
        evaluator = DebtEvaluator()
        plan = _make_plan(target_files=["src/foo.py"])
        finding = DebtFinding(
            gap="g", kind="sharp_edge", effort="small",
            touched_files=["src/other.py"],
        )
        result = evaluator._classify(finding, plan, "Add feature X")
        assert result.recommendation == "defer"

    def test_touched_test_sibling_folds_in(self) -> None:
        evaluator = DebtEvaluator()
        plan = _make_plan(target_files=["src/foo.py"])
        finding = DebtFinding(
            gap="add tests", kind="sharp_edge", effort="small",
            touched_files=["tests/unit/test_foo.py"],
        )
        result = evaluator._classify(finding, plan, "Add feature X")
        assert result.recommendation == "fold_in"

    def test_new_capability_signal_defers(self) -> None:
        evaluator = DebtEvaluator()
        plan = _make_plan(target_files=["src/foo.py"], title="Add X")
        finding = DebtFinding(
            gap="implement a new feature", kind="sharp_edge", effort="small",
            touched_files=["src/foo.py"],
        )
        result = evaluator._classify(finding, plan, "Add X")
        assert result.recommendation == "defer"


class TestIntroducesNewCap:
    def test_missing_feature_always_new(self) -> None:
        plan = _make_plan()
        assert DebtEvaluator._introduces_new_capability("x", "missing_feature", plan, "goal") is True

    def test_sharp_edge_no_signal(self) -> None:
        plan = _make_plan()
        assert DebtEvaluator._introduces_new_capability("refine error handling", "sharp_edge", plan, "goal") is False

    def test_sharp_edge_with_signal(self) -> None:
        plan = _make_plan()
        assert DebtEvaluator._introduces_new_capability("add support for retries", "sharp_edge", plan, "goal") is True

    def test_signal_in_goal_not_new(self) -> None:
        plan = _make_plan()
        assert DebtEvaluator._introduces_new_capability(
            "add support for retries", "sharp_edge", plan,
            "goal: add support for retries"
        ) is False


class TestEvaluateFallback:
    def test_no_evaluate_fn_uses_fallback(self) -> None:
        evaluator = DebtEvaluator(evaluate_fn=None)
        plan = _make_plan(target_files=["src/foo.py"], contracts=[])
        result = evaluator.evaluate(plan, "")
        assert len(result.findings) == 1
        assert "no test for src/foo.py" in result.findings[0].gap

    def test_evaluate_fn_raises_falls_back(self) -> None:
        def _fail(plan: Any, goal: str, ctx: str) -> list[dict[str, Any]]:
            raise RuntimeError("boom")

        evaluator = DebtEvaluator(evaluate_fn=_fail)
        plan = _make_plan(target_files=["src/foo.py"], contracts=[])
        result = evaluator.evaluate(plan, "")
        assert len(result.findings) == 1

    def test_evaluate_fn_returns_non_list_falls_back(self) -> None:
        def _bad(plan: Any, goal: str, ctx: str) -> str:
            return "not a list"

        evaluator = DebtEvaluator(evaluate_fn=_bad)  # type: ignore[arg-type]
        plan = _make_plan(target_files=["src/foo.py"], contracts=[])
        result = evaluator.evaluate(plan, "")
        assert len(result.findings) >= 1

    def test_evaluate_fn_returns_empty_falls_back(self) -> None:
        def _empty(plan: Any, goal: str, ctx: str) -> list[dict[str, Any]]:
            return []

        evaluator = DebtEvaluator(evaluate_fn=_empty)
        plan = _make_plan(target_files=["src/foo.py"], contracts=[])
        result = evaluator.evaluate(plan, "")
        assert len(result.findings) >= 1

    def test_evaluate_fn_success(self) -> None:
        def _ok(plan: Any, goal: str, ctx: str) -> list[dict[str, Any]]:
            return [
                {"gap": "fold-me", "kind": "sharp_edge", "effort": "small",
                 "touched_files": plan.target_files[:1]}
            ]

        evaluator = DebtEvaluator(evaluate_fn=_ok)
        plan = _make_plan(target_files=["src/foo.py"], contracts=[])
        result = evaluator.evaluate(plan, "Add X")
        assert len(result.findings) == 1
        assert result.findings[0].recommendation == "fold_in"

    def test_fallback_resilience_contracts(self) -> None:
        evaluator = DebtEvaluator(evaluate_fn=None)
        plan = _make_plan(
            target_files=["src/foo.py", "tests/unit/test_foo.py"],
            contracts=["Handle errors gracefully", "Timeout after 30s"],
        )
        result = evaluator.evaluate(plan, "")
        assert any("resilience" in f.gap for f in result.findings)

    def test_max_findings_truncates(self) -> None:
        evaluator = DebtEvaluator(evaluate_fn=None, max_findings=2)
        plan = _make_plan(
            target_files=["src/a.py", "src/b.py", "src/c.py"],
            contracts=["Handle errors", "Handle timeouts", "Handle retries"],
        )
        result = evaluator.evaluate(plan, "")
        assert len(result.findings) <= 2


class TestMakeDebtEvaluateFn:
    def test_returns_callable(self) -> None:
        gw = MagicMock()
        gw.call_model.return_value = MagicMock(content='{"findings": []}')
        fn = make_debt_evaluate_fn(gw)
        plan = _make_plan()
        result = fn(plan, "goal")
        assert result == []

    def test_returns_empty_on_gateway_error(self) -> None:
        gw = MagicMock()
        gw.call_model.side_effect = RuntimeError("boom")
        fn = make_debt_evaluate_fn(gw)
        plan = _make_plan()
        assert fn(plan, "goal") == []


class TestConstants:
    def test_rationale_strings_defined(self) -> None:
        assert _FOLD_IN_RATIONALE
        assert _DEFER_RATIONALE
        assert "in-scope" in _FOLD_IN_RATIONALE.lower()
        assert "defer" in _DEFER_RATIONALE.lower()
