"""Deep behavioral tests for quality gate enforcement, preflight checks,
failure aggregation, threshold enforcement, baseline comparison, and report generation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from general_ludd.quality.gate import QualityGateChecker
from general_ludd.quality.preflight import (
    check_coverage,
    check_session_drift,
    check_tasks_ticks,
    generate_backlog_from_audit,
    verify_task_completion,
)
from general_ludd.quality.project_gate import run_project_gate
from general_ludd.schemas.quality_gate import QualityGateConfig

REPO_ROOT = Path(__file__).parent.parent.parent


# ── Gate phase ordering ────────────────────────────────────────────────────


class TestGatePhaseOrdering:
    def test_preflight_checks_are_ordered_and_named(self) -> None:
        from general_ludd.quality.preflight import run_preflight

        report = run_preflight()
        raw_checks = cast(list[dict[str, Any]], report["checks"])
        names = [c["name"] for c in raw_checks]
        assert len(names) >= 10, f"expected ≥10 checks, got {len(names)}"
        assert names[0] == "coverage_85pct"
        assert names[1] == "lint_clean"
        assert names[2] == "mypy_clean"
        assert names[3] == "templates_exist"
        assert names[4] == "playbooks_exist"
        assert names[5] == "molecule_scenarios"
        assert names[6] == "filestore_readable"
        assert names[7] == "sprint_boxes_checked"
        assert names[8] == "completion_audit"
        assert names[9] == "tasks_ticks_valid"

    def test_preflight_last_checks_in_correct_order(self) -> None:
        from general_ludd.quality.preflight import run_preflight

        report = run_preflight()
        raw_checks = cast(list[dict[str, Any]], report["checks"])
        names = [c["name"] for c in raw_checks]
        assert names[-3] == "session_gate_drift"
        assert names[-2] == "readme_no_hardcoded_metrics"
        assert names[-1] == "terraform_collection_import_audit"

    def test_preflight_counts_match_report(self) -> None:
        from general_ludd.quality.preflight import run_preflight

        report = run_preflight()
        raw_checks = cast(list[dict[str, Any]], report["checks"])
        total_passed = sum(1 for c in raw_checks if c.get("passed"))
        assert report["passed_count"] == total_passed
        assert report["total_count"] == len(raw_checks)
        assert isinstance(report["overall"], str)
        assert cast(str, report["overall"]) in ("PASS", "FAIL")


# ── Failure aggregation ─────────────────────────────────────────────────────


class TestFailureAggregation:
    @staticmethod
    def _result(passed: bool, **extra: object) -> dict[str, object]:
        r: dict[str, object] = {"passed": passed}
        r.update(extra)
        return r

    def test_enforce_all_passed_green(self) -> None:
        config = QualityGateConfig()
        checker = QualityGateChecker(config)
        results: list[dict[str, object]] = [
            self._result(True),
            self._result(True),
            self._result(True),
        ]
        verdict = checker.enforce(results)
        assert verdict["all_passed"] is True
        assert verdict["blocks_completion"] is False
        assert verdict["blocks_commit"] is False
        assert verdict["blocks_merge"] is False
        assert verdict["blocks_push"] is False
        assert verdict["blocks_reload"] is False

    def test_enforce_single_failure_flips_all_blocks(self) -> None:
        config = QualityGateConfig()
        checker = QualityGateChecker(config)
        results: list[dict[str, object]] = [
            self._result(True),
            self._result(False),
            self._result(True),
        ]
        verdict = checker.enforce(results)
        assert verdict["all_passed"] is False
        assert verdict["blocks_completion"] is True
        assert verdict["blocks_commit"] is True
        assert verdict["blocks_merge"] is True
        assert verdict["blocks_push"] is True
        assert verdict["blocks_reload"] is True
        assert len(cast(list[object], verdict["gates"])) == 3

    def test_enforce_fail_closed_missing_passed_key(self) -> None:
        checker = QualityGateChecker()
        results: list[dict[str, object]] = [
            self._result(True),
            {"gate": "molecule_coverage"},
        ]
        verdict = checker.enforce(results)
        assert verdict["all_passed"] is False

    def test_enforce_empty_gates_fail_closed(self) -> None:
        checker = QualityGateChecker()
        verdict = checker.enforce([])
        assert verdict["all_passed"] is True
        assert len(cast(list[object], verdict["gates"])) == 0

    def test_enforce_blocks_respect_disabled_enforcement(self) -> None:
        config = QualityGateConfig()
        config.enforcement.block_todo_complete = False
        config.enforcement.block_commit = False
        config.enforcement.block_merge = False
        config.enforcement.block_push = False
        config.enforcement.block_reload = False
        checker = QualityGateChecker(config)
        results: list[dict[str, object]] = [self._result(False)]
        verdict = checker.enforce(results)
        assert verdict["all_passed"] is False
        assert verdict["blocks_completion"] is False
        assert verdict["blocks_commit"] is False

    def test_enforce_blocks_partial_disable(self) -> None:
        config = QualityGateConfig()
        config.enforcement.block_reload = False
        checker = QualityGateChecker(config)
        results: list[dict[str, object]] = [self._result(False)]
        verdict = checker.enforce(results)
        assert verdict["blocks_reload"] is False
        assert verdict["blocks_push"] is True


# ── Threshold enforcement ───────────────────────────────────────────────────


class TestThresholdEnforcement:
    def test_python_coverage_line_below_threshold(self) -> None:
        config = QualityGateConfig()
        config.python.line_coverage_min_percent = 85.0
        checker = QualityGateChecker(config)
        result = checker.check_python_coverage(70.0)
        assert result["passed"] is False
        checks = cast(list[dict[str, Any]], result["checks"])
        line_check = next(c for c in checks if c["check"] == "line_coverage")
        assert line_check["actual"] == 70.0
        assert line_check["required"] == 85.0
        assert line_check["status"] == "failed"

    def test_python_coverage_branch_below_threshold(self) -> None:
        config = QualityGateConfig()
        config.python.branch_coverage_min_percent = 75.0
        checker = QualityGateChecker(config)
        result = checker.check_python_coverage(90.0, branch_percent=50.0)
        assert result["passed"] is False
        checks = cast(list[dict[str, Any]], result["checks"])
        branch_checks = [c for c in checks if c["check"] == "branch_coverage"]
        assert len(branch_checks) == 1
        assert branch_checks[0]["actual"] == 50.0
        assert branch_checks[0]["required"] == 75.0
        assert branch_checks[0]["status"] == "failed"

    def test_python_coverage_disabled_skips(self) -> None:
        config = QualityGateConfig()
        config.python.enabled = False
        checker = QualityGateChecker(config)
        result = checker.check_python_coverage(0.0)
        assert result["passed"] is True
        assert cast(list[dict[str, Any]], result["checks"])[0].get("skipped") is True

    def test_molecule_coverage_below_threshold_fails(self) -> None:
        config = QualityGateConfig()
        config.molecule.coverage_min_percent = 80.0
        checker = QualityGateChecker(config)
        result = checker.check_molecule_coverage(10, 100)
        assert result["passed"] is False
        assert result["percent"] == 10.0
        assert result["required_percent"] == 80.0

    def test_molecule_coverage_disabled_skips(self) -> None:
        config = QualityGateConfig()
        config.molecule.enabled = False
        checker = QualityGateChecker(config)
        result = checker.check_molecule_coverage(0, 100)
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_coverage_xml_below_threshold_fails(self, tmp_path: Path) -> None:
        cov = tmp_path / "coverage.xml"
        cov.write_text('<?xml version="1.0" ?><coverage line-rate="0.42"></coverage>')
        result = check_coverage(threshold=85.0, coverage_xml=cov)
        assert result["passed"] is False
        assert result["coverage_pct"] == 42.0

    def test_coverage_xml_missing_line_rate(self, tmp_path: Path) -> None:
        cov = tmp_path / "coverage.xml"
        cov.write_text('<?xml version="1.0" ?><coverage></coverage>')
        result = check_coverage(threshold=85.0, coverage_xml=cov)
        assert result["passed"] is False
        assert result["coverage_pct"] == 0.0


# ── Baseline comparison ─────────────────────────────────────────────────────


class TestBaselineComparison:
    def test_molecule_scenarios_min_ratchet(self) -> None:
        from general_ludd.quality.preflight import MIN_MOLECULE_SCENARIOS, check_molecule_scenarios

        result = check_molecule_scenarios()
        assert cast(int, result["scenario_count"]) >= MIN_MOLECULE_SCENARIOS, (
            f"expected ≥{MIN_MOLECULE_SCENARIOS} scenarios, got {result['scenario_count']}"
        )

    def test_molecule_coverage_percent_computation(self) -> None:
        config = QualityGateConfig()
        config.molecule.coverage_min_percent = 50.0
        checker = QualityGateChecker(config)
        result = checker.check_molecule_coverage(7, 10)
        assert result["covered"] == 7
        assert result["required"] == 10
        assert result["percent"] == 70.0
        assert result["passed"] is True

    def test_molecule_coverage_zero_required_edge(self) -> None:
        checker = QualityGateChecker()
        result = checker.check_molecule_coverage(0, 0)
        assert result["passed"] is True
        assert result.get("covered") == 0
        assert result.get("required") == 0

    def test_line_coverage_at_exact_threshold(self) -> None:
        config = QualityGateConfig()
        config.python.line_coverage_min_percent = 85.0
        checker = QualityGateChecker(config)
        result = checker.check_python_coverage(85.0)
        assert result["passed"] is True
        checks = cast(list[dict[str, Any]], result["checks"])
        line_check = next(c for c in checks if c["check"] == "line_coverage")
        assert line_check["status"] == "passed"


# ── Report generation ───────────────────────────────────────────────────────


class TestReportGeneration:
    def test_backlog_from_audit_generates_tasks(self) -> None:
        audit: dict[str, object] = {
            "findings": [
                {"class_name": "UnusedClass", "file": "src/foo.py", "reason": "never used", "severity": "warn"},
                {"class_name": "DeadModule", "file": "src/bar.py", "reason": "no callers", "severity": "fail"},
            ],
        }
        todos = generate_backlog_from_audit(audit)
        assert len(todos) == 2
        assert todos[0]["title"] == "Wire UnusedClass into the pipeline"
        assert todos[0]["work_type"] == "code"
        assert todos[0]["priority"] == "medium"
        assert todos[1]["priority"] == "high"
        assert todos[1]["source_file"] == "src/bar.py"

    def test_backlog_from_empty_audit(self) -> None:
        todos = generate_backlog_from_audit({"findings": []})
        assert todos == []

    def test_verify_task_completion_all_met(self) -> None:
        criteria = ["Tests pass with >85% coverage", "No lint errors", "No type errors"]
        evidence: dict[str, object] = {
            "coverage_pct": 92.6,
            "lint_errors": 0,
            "mypy_errors": 0,
            "test_pass_count": 100,
            "test_fail_count": 0,
        }
        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is True
        assert result["passed"] == 3
        assert result["total"] == 3
        assert result["confidence"] == 1.0

    def test_verify_task_completion_partial(self) -> None:
        criteria = ["Tests pass with >85% coverage", "Lint clean", "Mypy 0 errors"]
        evidence: dict[str, object] = {"coverage_pct": 50.0, "lint_errors": 5, "mypy_errors": 1}
        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is False
        assert result["passed"] == 0
        assert result["confidence"] == 0.0

    def test_verify_task_completion_mixed(self) -> None:
        criteria = ["Tests pass with >85% coverage", "Lint clean"]
        evidence: dict[str, object] = {"coverage_pct": 94.0, "lint_errors": 3}
        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is False
        assert result["passed"] == 1
        assert result["total"] == 2
        assert result["confidence"] == 0.5

    def test_verify_task_completion_empty_criteria(self) -> None:
        result = verify_task_completion([], {})
        assert result["complete"] is False
        assert result["confidence"] == 0.0
        assert "No acceptance criteria" in cast(str, result["reason"])

    def test_verify_task_completion_unknown_criterion(self) -> None:
        criteria = ["Something totally unmatched by any keyword"]
        evidence: dict[str, object] = {"foo": "bar"}
        result = verify_task_completion(criteria, evidence)
        assert result["complete"] is False
        cr = cast(list[dict[str, Any]], result["criteria_results"])
        assert cr[0]["reason"] == "unknown_criterion"


# ── Task tick evidence validation ───────────────────────────────────────────


class TestTaskTickValidation:
    def test_tick_with_evidence_keyword_passes(self) -> None:
        lines = ["- [x] Feature X | evidence: make gate PASS abcdef1"]
        result = check_tasks_ticks(lines)
        assert result["passed"] is True
        assert cast(list[str], result["violations"]) == []

    def test_tick_with_backtick_hex_passes(self) -> None:
        lines = ["- [x] Feature Y — `abcdef1234567890`"]
        result = check_tasks_ticks(lines)
        assert result["passed"] is True

    def test_tick_with_plain_hex_passes(self) -> None:
        lines = ["- [x] Feature Z — committed abcdef1 to master"]
        result = check_tasks_ticks(lines)
        assert result["passed"] is True

    def test_tick_missing_all_evidence_fails(self) -> None:
        lines = ["- [x] Feature with no evidence at all"]
        result = check_tasks_ticks(lines)
        assert result["passed"] is False
        violations = cast(list[str], result["violations"])
        assert len(violations) == 1
        assert "Missing 'evidence:'" in violations[0]

    def test_tick_with_forbidden_word_fails(self) -> None:
        lines = ["- [x] Feature pending review | evidence: make gate PASS abcdef1"]
        result = check_tasks_ticks(lines)
        assert result["passed"] is False
        violations = cast(list[str], result["violations"])
        assert any("Forbidden word 'pending'" in v for v in violations)

    def test_tick_with_rejected_is_exempt(self) -> None:
        lines = ["- [x] Request denied | REJECTED: out of scope"]
        result = check_tasks_ticks(lines)
        assert result["passed"] is True

    def test_unchecked_lines_are_ignored(self) -> None:
        lines = [
            "- [ ] Pending task",
            "- [x] Done task | evidence: make gate PASS abcdef1",
        ]
        result = check_tasks_ticks(lines)
        assert result["passed"] is True
        assert cast(int, result["checked"]) == 1

    def test_legacy_audited_ledger_bypasses_strict_check(self) -> None:
        lines = [
            "Evidence-Integrity Audit completed 2025-01-01",
            "- [x] Legacy task without evidence",
        ]
        result = check_tasks_ticks(lines)
        assert result["passed"] is True

    def test_tick_with_forbidden_word_partial_scrubbed_by_backtick(self) -> None:
        lines = [
            "- [x] Feature — `pending` task | evidence: make gate PASS abcdef1",
        ]
        result = check_tasks_ticks(lines)
        assert result["passed"] is True

    def test_tick_with_groundwork_forbidden(self) -> None:
        lines = [
            "- [x] Feature groundwork completed | evidence: make gate PASS abcdef1",
        ]
        result = check_tasks_ticks(lines)
        assert result["passed"] is False
        violations = cast(list[str], result["violations"])
        assert any("Forbidden word 'groundwork'" in v for v in violations)


# ── Session drift detection ─────────────────────────────────────────────────


class TestSessionDrift:
    def test_missing_gate_markers_detected(self, tmp_path: Path) -> None:
        session = tmp_path / "SESSION.md"
        gate = tmp_path / ".gate-status"
        session.write_text("# SESSION\nNo gate block here.")
        gate.write_text("lint PASS 0")

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_session_drift()
        assert result["passed"] is False
        assert "gate markers" in cast(list[str], result["violations"])[0]

    def test_gate_drift_detects_missing_phase(self, tmp_path: Path) -> None:
        session = tmp_path / "SESSION.md"
        gate = tmp_path / ".gate-status"
        session.write_text("<!-- gate:begin -->\nlint\n<!-- gate:end -->")
        gate.write_text("lint PASS 0\ntypecheck PASS 22\ncollect PASS 0\nepoch 1234\n")

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_session_drift()
        assert result["passed"] is False
        violations = cast(list[str], result["violations"])
        assert any("typecheck" in v for v in violations)

    def test_gate_drift_all_phases_present_passes(self, tmp_path: Path) -> None:
        session = tmp_path / "SESSION.md"
        gate = tmp_path / ".gate-status"
        session.write_text("<!-- gate:begin -->\nlint\ntypecheck\ncollect\n<!-- gate:end -->")
        gate.write_text("lint PASS 0\ntypecheck PASS 22\ncollect PASS 0\nepoch 1234\n")

        with patch("general_ludd.quality.preflight.REPO_ROOT", tmp_path):
            result = check_session_drift()
        assert result["passed"] is True

    def test_missing_both_files(self) -> None:
        with patch("general_ludd.quality.preflight.REPO_ROOT", Path("/nonexistent")):
            result = check_session_drift()
        assert result["passed"] is True
        assert result.get("reason") == "files missing"


# ── Project gate aggregation ────────────────────────────────────────────────


class TestProjectGate:
    def test_project_gate_all_required_pass(self, tmp_path: Path) -> None:
        from general_ludd.project_runner import ProjectCommandRunner, ProjectProfile
        from general_ludd.project_runner.runner import CheckResult

        profile = ProjectProfile(
            name="test-proj",
            commands={"lint": "echo lint-ok", "test": "echo test-ok"},
        )
        with patch.object(ProjectCommandRunner, "run") as mock_run:
            mock_run.side_effect = [
                CheckResult(name="lint", exit_code=0, passed=True, duration_s=0.1),
                CheckResult(name="test", exit_code=0, passed=True, duration_s=0.2),
            ]
            result = run_project_gate(
                str(tmp_path),
                checks=("lint", "test"),
                profile=profile,
            )
        assert result["passed"] is True
        assert result["overall"] == "PASS"
        assert result["passed_count"] == 2
        assert result["failed_count"] == 0

    def test_project_gate_required_failure_fails_gate(self, tmp_path: Path) -> None:
        from general_ludd.project_runner import ProjectCommandRunner, ProjectProfile
        from general_ludd.project_runner.runner import CheckResult

        profile = ProjectProfile(
            name="test-proj",
            commands={"lint": "echo lint-ok", "test": "echo fail; exit 1"},
        )
        with patch.object(ProjectCommandRunner, "run") as mock_run:
            mock_run.side_effect = [
                CheckResult(name="lint", exit_code=0, passed=True, duration_s=0.1),
                CheckResult(name="test", exit_code=1, passed=False, duration_s=0.3, error="test failed"),
            ]
            result = run_project_gate(
                str(tmp_path),
                checks=("lint", "test"),
                profile=profile,
            )
        assert result["passed"] is False
        assert result["overall"] == "FAIL"
        assert result["passed_count"] == 1
        assert result["failed_count"] == 1

    def test_project_gate_missing_required_check_fails(self, tmp_path: Path) -> None:
        from general_ludd.project_runner import ProjectProfile

        profile = ProjectProfile(
            name="test-proj",
            commands={"lint": "echo lint-ok"},
        )
        result = run_project_gate(
            str(tmp_path),
            checks=("lint", "test"),
            profile=profile,
        )
        assert result["passed"] is False
        missing = cast(list[str], result["missing"])
        checks = cast(list[dict[str, Any]], result["checks"])
        assert "test" in missing
        assert any(c["name"] == "test" and c["declared"] is False for c in checks)

    def test_project_gate_required_not_in_run_set_fail_closed(self, tmp_path: Path) -> None:
        from general_ludd.project_runner import ProjectCommandRunner, ProjectProfile
        from general_ludd.project_runner.runner import CheckResult

        profile = ProjectProfile(
            name="test-proj",
            commands={"lint": "echo lint-ok", "test": "echo test-ok"},
        )
        with patch.object(ProjectCommandRunner, "run") as mock_run:
            mock_run.side_effect = [
                CheckResult(name="lint", exit_code=0, passed=True, duration_s=0.1),
            ]
            result = run_project_gate(
                str(tmp_path),
                checks=("lint",),
                required=("lint", "test"),
                profile=profile,
            )
        assert result["passed"] is False
        missing = cast(list[str], result["missing"])
        assert "test" in missing
        assert result["failed_count"] == 1
