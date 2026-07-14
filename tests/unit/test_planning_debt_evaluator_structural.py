"""Structural tests for planning/debt_evaluator.py — DebtEvaluator + DebtFinding."""

from __future__ import annotations

import inspect

import pytest

from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.debt_evaluator import (
    _FEATURE_SIGNALS,
    _FOLD_IN_RATIONALE,
    _RESILIENCE_WORDS,
    _SYSTEM_PROMPT,
    DebtEffort,
    DebtEvaluator,
    DebtFinding,
    DebtFindings,
    DebtKind,
    DebtRecommendation,
    EvaluateFn,
    _basename,
    _build_user_prompt,
    _is_impl_py,
    _is_test_file,
    _stem,
    _test_name_of,
    make_debt_evaluate_fn,
)


def _make_plan(**kwargs) -> PlanArtifact:
    defaults = {
        "todo_id": "TD-001",
        "title": "Add retry logic",
        "description": "Add exponential backoff to API calls",
        "target_files": ["src/api.py", "tests/test_api.py"],
        "contracts": ["error handling with timeout"],
    }
    defaults.update(kwargs)
    return PlanArtifact(**defaults)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_fold_in_rationale_non_empty(self):
        assert isinstance(_FOLD_IN_RATIONALE, str)
        assert len(_FOLD_IN_RATIONALE) > 0
        assert "in-scope" in _FOLD_IN_RATIONALE.lower()

    def test_defer_rationale_accessible(self):
        # _DEFER_RATIONALE is private but testable via _classify output
        from general_ludd.planning.debt_evaluator import _DEFER_RATIONALE

        assert isinstance(_DEFER_RATIONALE, str)
        assert "defer" in _DEFER_RATIONALE.lower()

    def test_feature_signals_is_tuple_of_strings(self):
        assert isinstance(_FEATURE_SIGNALS, tuple)
        assert len(_FEATURE_SIGNALS) > 0
        assert all(isinstance(s, str) for s in _FEATURE_SIGNALS)
        assert "new feature" in _FEATURE_SIGNALS

    def test_resilience_words_is_tuple_of_strings(self):
        assert isinstance(_RESILIENCE_WORDS, tuple)
        assert len(_RESILIENCE_WORDS) > 0
        assert all(isinstance(s, str) for s in _RESILIENCE_WORDS)
        assert "retry" in _RESILIENCE_WORDS

    def test_system_prompt_non_empty(self):
        assert isinstance(_SYSTEM_PROMPT, str)
        assert len(_SYSTEM_PROMPT) > 0
        assert "forward" in _SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Type aliases (verify via DebtFinding field validation)
# ---------------------------------------------------------------------------


class TestTypeAliases:
    def test_debt_kind_values(self):
        from typing import get_args

        kinds = get_args(DebtKind)
        assert "missing_feature" in kinds
        assert "sharp_edge" in kinds

    def test_debt_recommendation_values(self):
        from typing import get_args

        recs = get_args(DebtRecommendation)
        assert "fold_in" in recs
        assert "defer" in recs

    def test_debt_effort_values(self):
        from typing import get_args

        efforts = get_args(DebtEffort)
        assert "small" in efforts
        assert "medium" in efforts
        assert "large" in efforts

    def test_evaluate_fn_is_protocol(self):
        assert EvaluateFn is not None


# ---------------------------------------------------------------------------
# DebtFinding model
# ---------------------------------------------------------------------------


class TestDebtFinding:
    def test_default_construction(self):
        finding = DebtFinding(gap="no retry logic", kind="sharp_edge")
        assert finding.gap == "no retry logic"
        assert finding.kind == "sharp_edge"
        assert finding.why_it_matters == ""
        assert finding.recommendation == "defer"
        assert finding.feature_creep_rationale == ""
        assert finding.effort == "medium"
        assert finding.touched_files == []

    def test_full_construction(self):
        finding = DebtFinding(
            gap="missing timeout",
            kind="sharp_edge",
            why_it_matters="timeouts cause hangs",
            recommendation="fold_in",
            feature_creep_rationale="in scope",
            effort="small",
            touched_files=["src/api.py"],
        )
        assert finding.gap == "missing timeout"
        assert finding.recommendation == "fold_in"
        assert finding.touched_files == ["src/api.py"]

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValueError):
            DebtFinding(gap="x", kind="bogus_kind")

    def test_invalid_recommendation_rejected(self):
        with pytest.raises(ValueError):
            DebtFinding(gap="x", kind="sharp_edge", recommendation="bogus")

    def test_invalid_effort_rejected(self):
        with pytest.raises(ValueError):
            DebtFinding(gap="x", kind="sharp_edge", effort="huge")

    def test_model_fields(self):
        fields = set(DebtFinding.model_fields.keys())
        expected = {
            "gap",
            "kind",
            "why_it_matters",
            "recommendation",
            "feature_creep_rationale",
            "effort",
            "touched_files",
        }
        assert fields == expected

    def test_strict_config(self):
        assert DebtFinding.model_config.get("strict") is True


# ---------------------------------------------------------------------------
# DebtFindings model
# ---------------------------------------------------------------------------


class TestDebtFindings:
    def test_default_construction(self):
        findings = DebtFindings()
        assert findings.findings == []

    def test_with_findings(self):
        f = DebtFinding(gap="g1", kind="sharp_edge")
        findings = DebtFindings(findings=[f])
        assert len(findings.findings) == 1
        assert findings.findings[0].gap == "g1"

    def test_model_fields(self):
        fields = set(DebtFindings.model_fields.keys())
        assert fields == {"findings"}


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


class TestBasename:
    def test_simple_path(self):
        assert _basename("src/api.py") == "api.py"

    def test_nested_path(self):
        assert _basename("a/b/c/d/file.txt") == "file.txt"

    def test_no_slash(self):
        assert _basename("file.py") == "file.py"


class TestIsTestFile:
    def test_test_file(self):
        assert _is_test_file("tests/test_api.py") is True

    def test_non_test(self):
        assert _is_test_file("src/api.py") is False

    def test_prefix_only(self):
        assert _is_test_file("testutil.py") is False

    def test_no_py_extension(self):
        assert _is_test_file("tests/test_api.txt") is False


class TestIsImplPy:
    def test_source_file(self):
        assert _is_impl_py("src/api.py") is True

    def test_test_file_excluded(self):
        assert _is_impl_py("tests/test_api.py") is False

    def test_non_py_file(self):
        assert _is_impl_py("README.md") is False


class TestStem:
    def test_py_stem(self):
        assert _stem("src/api.py") == "api"

    def test_non_py(self):
        assert _stem("README.md") == "README.md"

    def test_nested_py(self):
        assert _stem("a/b/models.py") == "models"


class TestTestNameOf:
    def test_extracts_impl_name(self):
        assert _test_name_of("tests/test_config.py") == "config"

    def test_compound_name(self):
        assert _test_name_of("tests/app/test_event_handler.py") == "event_handler"


# ---------------------------------------------------------------------------
# DebtEvaluator
# ---------------------------------------------------------------------------


class TestDebtEvaluatorInit:
    def test_default_construction(self):
        evaluator = DebtEvaluator()
        assert evaluator._evaluate_fn is None
        assert evaluator._max_findings == 8
        assert evaluator.profile_id == "compactor"

    def test_custom_params(self):
        evaluator = DebtEvaluator(
            evaluate_fn=None, max_findings=5, profile_id="custom"
        )
        assert evaluator._max_findings == 5
        assert evaluator.profile_id == "custom"

    def test_public_attributes(self):
        evaluator = DebtEvaluator()
        assert hasattr(evaluator, "profile_id")
        assert hasattr(evaluator, "evaluate")


class TestDebtEvaluatorMethodsExist:
    def test_methods_exist(self):
        evaluator = DebtEvaluator()
        assert callable(evaluator.evaluate)
        assert callable(evaluator._classify)
        assert callable(evaluator._fallback)
        assert callable(DebtEvaluator._touched_in_scope)
        assert callable(DebtEvaluator._introduces_new_capability)

    def test_evaluate_signature(self):
        sig = inspect.signature(DebtEvaluator.evaluate)
        params = list(sig.parameters.keys())
        assert "plan" in params
        assert "goal" in params
        assert "repo_context" in params
        assert sig.return_annotation == "DebtFindings"

    def test_classify_signature(self):
        sig = inspect.signature(DebtEvaluator._classify)
        params = list(sig.parameters.keys())
        assert "finding" in params
        assert "plan" in params
        assert "goal" in params
        assert sig.return_annotation == "DebtFinding"


class TestDebtEvaluatorTouchedInScope:
    def test_empty_touched_not_in_scope(self):
        result = DebtEvaluator._touched_in_scope([], ["src/a.py"])
        assert result is False

    def test_touched_in_target_files(self):
        result = DebtEvaluator._touched_in_scope(
            ["src/a.py"], ["src/a.py", "src/b.py"]
        )
        assert result is True

    def test_test_file_sibling_accepted(self):
        result = DebtEvaluator._touched_in_scope(
            ["tests/test_a.py"], ["src/a.py"]
        )
        assert result is True

    def test_unknown_file_rejected(self):
        result = DebtEvaluator._touched_in_scope(
            ["src/other.py"], ["src/a.py"]
        )
        assert result is False


class TestDebtEvaluatorIntroducesNewCapability:
    def test_missing_feature_is_new_capability(self):
        plan = _make_plan()
        result = DebtEvaluator._introduces_new_capability(
            "add new feature X", "missing_feature", plan, "goal text"
        )
        assert result is True

    def test_sharp_edge_in_plan_not_new(self):
        plan = _make_plan(
            title="Add retry logic",
            description="Add exponential backoff",
            contracts=["error handling with retry"],
        )
        result = DebtEvaluator._introduces_new_capability(
            "add retry for API calls", "sharp_edge", plan, "add retry support"
        )
        assert result is False

    def test_sharp_edge_with_new_signal(self):
        plan = _make_plan(
            title="Fix bug",
            description="Fix a typo",
            contracts=[],
        )
        result = DebtEvaluator._introduces_new_capability(
            "build a new dashboard panel", "sharp_edge", plan, "fix typo"
        )
        assert result is True


class TestDebtEvaluatorClassify:
    def test_small_in_scope_sharp_edge_folds_in(self):
        plan = _make_plan(target_files=["src/api.py", "tests/test_api.py"])
        evaluator = DebtEvaluator()
        finding = DebtFinding(
            gap="no retry",
            kind="sharp_edge",
            effort="small",
            touched_files=["src/api.py"],
        )
        result = evaluator._classify(finding, plan, "add retry")
        assert result.recommendation == "fold_in"

    def test_large_effort_defers(self):
        plan = _make_plan(target_files=["src/api.py", "tests/test_api.py"])
        evaluator = DebtEvaluator()
        finding = DebtFinding(
            gap="no retry",
            kind="sharp_edge",
            effort="large",
            touched_files=["src/api.py"],
        )
        result = evaluator._classify(finding, plan, "add retry")
        assert result.recommendation == "defer"

    def test_out_of_scope_defers(self):
        plan = _make_plan(target_files=["src/api.py"])
        evaluator = DebtEvaluator()
        finding = DebtFinding(
            gap="no retry",
            kind="sharp_edge",
            effort="small",
            touched_files=["src/db.py"],
        )
        result = evaluator._classify(finding, plan, "add retry")
        assert result.recommendation == "defer"

    def test_missing_feature_defers(self):
        plan = _make_plan(target_files=["src/api.py", "tests/test_api.py"])
        evaluator = DebtEvaluator()
        finding = DebtFinding(
            gap="no dashboard",
            kind="missing_feature",
            effort="small",
            touched_files=["src/api.py"],
        )
        result = evaluator._classify(finding, plan, "add retry")
        assert result.recommendation == "defer"

    def test_returns_debtfinding_instance(self):
        plan = _make_plan()
        evaluator = DebtEvaluator()
        finding = DebtFinding(gap="no retry", kind="sharp_edge")
        result = evaluator._classify(finding, plan, "goal")
        assert isinstance(result, DebtFinding)

    def test_preserves_non_policy_fields(self):
        plan = _make_plan()
        evaluator = DebtEvaluator()
        finding = DebtFinding(
            gap="original gap",
            kind="sharp_edge",
            why_it_matters="matters a lot",
            effort="medium",
            touched_files=["src/app.py"],
        )
        result = evaluator._classify(finding, plan, "goal")
        assert result.gap == "original gap"
        assert result.why_it_matters == "matters a lot"
        assert result.effort == "medium"
        assert result.touched_files == ["src/app.py"]


class TestDebtEvaluatorEvaluate:
    def test_no_evaluate_fn_returns_fallback(self):
        evaluator = DebtEvaluator()
        plan = _make_plan()
        result = evaluator.evaluate(plan, "goal")
        assert isinstance(result, DebtFindings)

    def test_none_fn_never_raises(self):
        evaluator = DebtEvaluator(evaluate_fn=None)
        plan = _make_plan()
        result = evaluator.evaluate(plan, "goal")
        assert isinstance(result, DebtFindings)

    def test_failing_fn_falls_back(self):
        def bad_fn(plan, goal, repo_context):
            raise RuntimeError("model error")

        evaluator = DebtEvaluator(evaluate_fn=bad_fn)
        plan = _make_plan()
        result = evaluator.evaluate(plan, "goal")
        assert isinstance(result, DebtFindings)

    def test_non_list_return_falls_back(self):
        def str_fn(plan, goal, repo_context):
            return "not a list"

        evaluator = DebtEvaluator(evaluate_fn=str_fn)
        plan = _make_plan()
        result = evaluator.evaluate(plan, "goal")
        assert isinstance(result, DebtFindings)

    def test_empty_list_return_falls_back(self):
        def empty_fn(plan, goal, repo_context):
            return []

        evaluator = DebtEvaluator(evaluate_fn=empty_fn)
        plan = _make_plan()
        result = evaluator.evaluate(plan, "goal")
        assert isinstance(result, DebtFindings)

    def test_valid_result_classified_and_truncated(self):
        def good_fn(plan, goal, repo_context):
            return [
                {
                    "gap": "no retry",
                    "kind": "sharp_edge",
                    "effort": "small",
                    "touched_files": plan.target_files,
                }
            ]

        evaluator = DebtEvaluator(evaluate_fn=good_fn, max_findings=3)
        plan = _make_plan(target_files=["src/api.py", "tests/test_api.py"])
        result = evaluator.evaluate(plan, "add retry")
        assert len(result.findings) == 1
        assert result.findings[0].recommendation == "fold_in"

    def test_malformed_findings_falls_back(self):
        def malformed_fn(plan, goal, repo_context):
            return [{"gap": "no retry"}]  # missing required 'kind'

        evaluator = DebtEvaluator(evaluate_fn=malformed_fn)
        plan = _make_plan()
        result = evaluator.evaluate(plan, "goal")
        assert isinstance(result, DebtFindings)


class TestDebtEvaluatorFallback:
    def test_returns_debtfindings(self):
        evaluator = DebtEvaluator()
        plan = _make_plan()
        result = evaluator._fallback(plan)
        assert isinstance(result, DebtFindings)

    def test_untested_source_file_triggers_finding(self):
        evaluator = DebtEvaluator()
        plan = _make_plan(
            target_files=["src/api.py"],
            contracts=[],
        )
        result = evaluator._fallback(plan)
        untested = [f for f in result.findings if "no test for" in f.gap]
        assert len(untested) == 1
        assert untested[0].kind == "sharp_edge"
        assert untested[0].recommendation == "fold_in"

    def test_tested_source_file_no_missing_test_finding(self):
        evaluator = DebtEvaluator()
        plan = _make_plan(
            target_files=["src/api.py", "tests/test_api.py"],
            contracts=[],
        )
        result = evaluator._fallback(plan)
        untested = [f for f in result.findings if "no test for" in f.gap]
        assert len(untested) == 0

    def test_resilience_contract_triggers_finding(self):
        evaluator = DebtEvaluator()
        plan = _make_plan(
            target_files=["src/api.py", "tests/test_api.py"],
            contracts=["error handling with retry"],
        )
        result = evaluator._fallback(plan)
        resilience = [f for f in result.findings if "resilience" in f.gap]
        assert len(resilience) == 1
        assert resilience[0].kind == "sharp_edge"
        assert resilience[0].recommendation == "defer"

    def test_no_resilience_words_no_finding(self):
        evaluator = DebtEvaluator()
        plan = _make_plan(
            target_files=["src/api.py", "tests/test_api.py"],
            contracts=["proper logging"],
        )
        result = evaluator._fallback(plan)
        resilience = [f for f in result.findings if "resilience" in f.gap]
        assert len(resilience) == 0

    def test_respects_max_findings(self):
        evaluator = DebtEvaluator(max_findings=2)
        plan = _make_plan(
            target_files=[f"src/m{i}.py" for i in range(10)],
            contracts=["error handling", "timeout recovery", "retry logic"],
        )
        result = evaluator._fallback(plan)
        assert len(result.findings) <= 2


# ---------------------------------------------------------------------------
# Build user prompt
# ---------------------------------------------------------------------------


class TestBuildUserPrompt:
    def test_returns_string(self):
        plan = _make_plan()
        result = _build_user_prompt(plan, "add retry", "context")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_goal(self):
        plan = _make_plan()
        result = _build_user_prompt(plan, "add retry", "")
        assert "add retry" in result

    def test_includes_plan(self):
        plan = _make_plan(title="Add retry logic")
        result = _build_user_prompt(plan, "add retry", "")
        assert "Add retry logic" in result

    def test_includes_repo_context_when_provided(self):
        plan = _make_plan()
        result = _build_user_prompt(plan, "add retry", "large repo context")
        assert "large repo context" in result

    def test_omits_context_when_empty(self):
        plan = _make_plan()
        result = _build_user_prompt(plan, "add retry", "")
        assert "REPO CONTEXT" not in result


# ---------------------------------------------------------------------------
# make_debt_evaluate_fn
# ---------------------------------------------------------------------------


class TestMakeDebtEvaluateFn:
    def test_returns_callable(self):
        fn = make_debt_evaluate_fn(model_gateway=None)
        assert callable(fn)

    def test_returned_fn_has_correct_parameter_names(self):
        fn = make_debt_evaluate_fn(model_gateway=None)
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())
        assert "plan" in param_names
        assert "goal" in param_names
        assert "repo_context" in param_names

    def test_returned_fn_returns_empty_list_on_none_gateway(self):
        fn = make_debt_evaluate_fn(model_gateway=None)
        plan = _make_plan()
        result = fn(plan, "goal", "context")
        assert result == []
