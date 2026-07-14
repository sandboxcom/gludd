"""Structural tests for review/decision_applier.py — decision application and gate integration."""

from __future__ import annotations

import inspect
import logging

from general_ludd.review.decision_applier import (
    _DECISION_STATUS_MAP,
    _LOW_CONFIDENCE_THRESHOLD,
    _gate_failure_summary,
    apply_decision,
)
from general_ludd.schemas.todo import TodoStatus


class TestGateFailureSummary:
    """Structural tests for _gate_failure_summary helper."""

    def test_exists_and_callable(self):
        assert callable(_gate_failure_summary)

    def test_signature_accepts_dict_report(self):
        sig = inspect.signature(_gate_failure_summary)
        assert "report" in sig.parameters

    def test_empty_report_default_message(self):
        result = _gate_failure_summary({})
        assert result == "project gate FAILED"

    def test_report_with_no_checks_key(self):
        result = _gate_failure_summary({"passed": False})
        assert result == "project gate FAILED"

    def test_checks_not_list(self):
        result = _gate_failure_summary({"checks": "not a list"})
        assert result == "project gate FAILED"

    def test_all_checks_passing_produces_default(self):
        result = _gate_failure_summary({
            "checks": [
                {"name": "lint", "passed": True},
                {"name": "typecheck", "passed": True},
            ],
        })
        assert result == "project gate FAILED"

    def test_mixed_checks_extracts_failed_summaries(self):
        result = _gate_failure_summary({
            "checks": [
                {"name": "lint", "passed": False, "summary": "lint had 3 errors"},
                {"name": "typecheck", "passed": True},
                {"name": "test", "passed": False, "summary": "2 tests failed"},
            ],
        })
        assert "lint had 3 errors" in result
        assert "2 tests failed" in result
        assert ";" in result

    def test_failed_check_without_summary_uses_name(self):
        result = _gate_failure_summary({
            "checks": [
                {"name": "security", "passed": False},
            ],
        })
        assert "security: FAIL" in result

    def test_failed_check_without_name_defaults_to_check(self):
        result = _gate_failure_summary({
            "checks": [
                {"passed": False},
            ],
        })
        assert "check: FAIL" in result

    def test_returns_string(self):
        result = _gate_failure_summary({})
        assert isinstance(result, str)


class TestDecisionStatusMap:
    """Structural tests for _DECISION_STATUS_MAP constant."""

    def test_is_dict(self):
        assert isinstance(_DECISION_STATUS_MAP, dict)

    def test_maps_complete_to_todostatus_complete(self):
        assert _DECISION_STATUS_MAP["complete"] is TodoStatus.COMPLETE

    def test_maps_needs_more_work(self):
        assert _DECISION_STATUS_MAP["needs_more_work"] is TodoStatus.NEEDS_MORE_WORK

    def test_maps_failed(self):
        assert _DECISION_STATUS_MAP["failed"] is TodoStatus.FAILED

    def test_maps_blocked(self):
        assert _DECISION_STATUS_MAP["blocked"] is TodoStatus.BLOCKED

    def test_maps_manual_hold(self):
        assert _DECISION_STATUS_MAP["manual_hold"] is TodoStatus.MANUAL_HOLD

    def test_all_values_are_todostatus(self):
        for value in _DECISION_STATUS_MAP.values():
            assert isinstance(value, TodoStatus)

    def test_exactly_five_entries(self):
        assert len(_DECISION_STATUS_MAP) == 5

    def test_keys_are_strings(self):
        for key in _DECISION_STATUS_MAP:
            assert isinstance(key, str)


class TestLowConfidenceThreshold:
    """Structural tests for _LOW_CONFIDENCE_THRESHOLD constant."""

    def test_is_float(self):
        assert isinstance(_LOW_CONFIDENCE_THRESHOLD, float)

    def test_value_is_0_5(self):
        assert _LOW_CONFIDENCE_THRESHOLD == 0.5

    def test_between_zero_and_one(self):
        assert 0.0 <= _LOW_CONFIDENCE_THRESHOLD <= 1.0


class TestApplyDecisionSignature:
    """Structural tests for apply_decision function signature."""

    def test_is_async_function(self):
        assert inspect.iscoroutinefunction(apply_decision)

    def test_required_params(self):
        sig = inspect.signature(apply_decision)
        param_names = list(sig.parameters)
        assert "decision" in param_names
        assert "todo_repo" in param_names
        assert "session" in param_names

    def test_repo_root_is_keyword_only(self):
        sig = inspect.signature(apply_decision)
        repo_root_param = sig.parameters["repo_root"]
        assert repo_root_param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_repo_root_defaults_to_none(self):
        sig = inspect.signature(apply_decision)
        repo_root_param = sig.parameters["repo_root"]
        assert repo_root_param.default is None

    def test_decision_param_has_taskdecision_type_hint(self):
        sig = inspect.signature(apply_decision)
        annotation = sig.parameters["decision"].annotation
        assert "TaskDecision" in str(annotation)

    def test_session_param_has_asyncsession_type_hint(self):
        sig = inspect.signature(apply_decision)
        annotation = sig.parameters["session"].annotation
        assert "AsyncSession" in str(annotation)

    def test_repo_root_annotation_is_optional_str(self):
        sig = inspect.signature(apply_decision)
        anno = sig.parameters["repo_root"].annotation
        anno_str = str(anno)
        assert "str" in anno_str
        assert "None" in anno_str

    def test_return_annotation_is_none(self):
        sig = inspect.signature(apply_decision)
        assert str(sig.return_annotation) == "None"


class TestModuleLevelAttributes:
    """Structural tests for module-level logger and constants."""

    def test_logger_exists(self):
        import general_ludd.review.decision_applier as mod
        assert hasattr(mod, "logger")
        assert isinstance(mod.logger, logging.Logger)

    def test_logger_name_contains_decision_applier(self):
        import general_ludd.review.decision_applier as mod
        assert "decision_applier" in mod.logger.name

    def test_module_can_be_imported(self):
        import general_ludd.review.decision_applier as mod
        assert mod is not None

    def test_all_expected_exports_present(self):
        import general_ludd.review.decision_applier as mod
        expected = {
            "_gate_failure_summary", "apply_decision",
            "_DECISION_STATUS_MAP", "_LOW_CONFIDENCE_THRESHOLD",
        }
        for name in expected:
            assert hasattr(mod, name), f"missing export: {name}"
