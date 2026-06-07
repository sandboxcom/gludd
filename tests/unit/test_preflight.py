"""Tests for preflight quality gate and task completion verification."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


class TestPreflightQualityGate:
    def test_check_coverage_above_threshold(self):
        from general_ludd.quality.preflight import check_coverage

        result = check_coverage(threshold=10.0)
        assert result["passed"] is True
        assert result["threshold"] == 10.0
        assert "coverage_pct" in result

    def test_check_coverage_below_threshold_fails(self):
        from general_ludd.quality.preflight import check_coverage

        result = check_coverage(threshold=99.9)
        assert result["passed"] is False

    def test_check_lint_passes(self):
        from general_ludd.quality.preflight import check_lint

        result = check_lint()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "error_count" in result

    def test_check_mypy_passes(self):
        from general_ludd.quality.preflight import check_mypy

        result = check_mypy()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "error_count" in result

    def test_check_templates_exist(self):
        from general_ludd.quality.preflight import check_templates

        result = check_templates()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "found" in result
        assert "missing" in result

    def test_check_playbooks_exist(self):
        from general_ludd.quality.preflight import check_playbooks

        result = check_playbooks()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "found" in result

    def test_check_molecule_scenarios(self):
        from general_ludd.quality.preflight import check_molecule_scenarios

        result = check_molecule_scenarios()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "scenario_count" in result

    def test_check_filestore_readable(self):
        from general_ludd.quality.preflight import check_filestore

        result = check_filestore()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "root_path" in result

    def test_check_sprint_checkboxes(self):
        from general_ludd.quality.preflight import check_sprint_boxes

        result = check_sprint_boxes()
        assert isinstance(result, dict)
        assert "unchecked_count" in result

    def test_run_all_preflight_checks(self):
        from general_ludd.quality.preflight import run_preflight

        report = run_preflight()
        assert isinstance(report, dict)
        assert "overall" in report
        assert "checks" in report
        assert isinstance(report["checks"], list)
        assert len(report["checks"]) >= 5

    def test_preflight_report_format(self):
        from general_ludd.quality.preflight import run_preflight

        report = run_preflight()
        assert "overall" in report
        assert "checks" in report
        assert "passed_count" in report
        assert "total_count" in report
        assert len(report["checks"]) == report["total_count"]
        for check in report["checks"]:
            assert "name" in check
            assert "passed" in check
            assert len(check) >= 3  # at least name + passed + one detail


class TestTaskCompletionVerifier:
    def test_verify_acceptance_criteria_all_met(self):
        from general_ludd.quality.preflight import verify_task_completion

        criteria = [
            "Tests pass with >85% coverage",
            "No lint errors",
            "No type errors",
        ]
        evidence = {
            "test_pass_count": 2743,
            "test_fail_count": 0,
            "coverage_pct": 92.6,
            "lint_errors": 0,
            "mypy_errors": 0,
        }

        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is True
        assert result["confidence"] > 0.8

    def test_verify_acceptance_criteria_failing(self):
        from general_ludd.quality.preflight import verify_task_completion

        criteria = [
            "Tests pass with >85% coverage",
            "All files above 85% coverage",
        ]
        evidence = {
            "coverage_pct": 76.0,
        }

        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is False
        assert result["confidence"] < 0.5

    def test_verify_empty_criteria(self):
        from general_ludd.quality.preflight import verify_task_completion

        result = verify_task_completion([], {})
        assert result["complete"] is False
        assert result["confidence"] == 0.0

    def test_verify_evidence_matches_keywords(self):
        from general_ludd.quality.preflight import verify_task_completion

        criteria = [
            "All files must have >85% LINE coverage",
            "No FAILING tests",
        ]
        evidence = {
            "coverage_pct": 94.0,
            "test_fail_count": 0,
            "lint_errors": 0,
        }

        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is True
        assert len(result["criteria_results"]) == 2
