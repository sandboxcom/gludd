"""Unit tests for validation runner, gap analyzer, and log auditor."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

from agentic_harness.validation.gap_analyzer import GapAnalyzer, GapReport
from agentic_harness.validation.log_auditor import AuditReport, LogAuditor
from agentic_harness.validation.runner import ValidationResult, ValidationRunner

PLAYBOOK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "playbooks")


class TestValidationRunnerSuccess:
    @patch("agentic_harness.validation.runner.subprocess.run")
    def test_validation_runner_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2 passed",
            stderr="",
        )
        runner = ValidationRunner(
            todo_id="TODO-001",
            worktree_path="/tmp/worktree",
            test_commands=["pytest tests/"],
        )
        result = runner.run_validation()
        assert isinstance(result, ValidationResult)
        assert result.success is True
        assert result.passed_count == 2
        assert result.failed_count == 0


class TestValidationRunnerFailureCreatesChildTodos:
    @patch("agentic_harness.validation.runner.subprocess.run")
    def test_validation_runner_failure_creates_child_todos(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="1 passed, 2 failed\nFAILED test_a.py::test_one\nFAILED test_b.py::test_two",
            stderr="",
        )
        runner = ValidationRunner(
            todo_id="TODO-001",
            worktree_path="/tmp/worktree",
            test_commands=["pytest tests/"],
        )
        result = runner.run_validation()
        assert result.success is False
        assert result.failed_count == 2
        children = runner.create_child_todos_for_failures(result)
        assert len(children) == 2
        assert all(c["parent_todo_id"] == "TODO-001" for c in children)


class TestValidationRunnerMissingTestsCreatesTodo:
    @patch("agentic_harness.validation.runner.subprocess.run")
    def test_validation_runner_missing_tests_creates_todo(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0 passed",
            stderr="",
        )
        runner = ValidationRunner(
            todo_id="TODO-002",
            worktree_path="/tmp/worktree",
            test_commands=["pytest tests/"],
        )
        result = runner.run_validation()
        assert result.passed_count == 0
        children = runner.create_child_todos_for_failures(result)
        assert len(children) == 1
        assert children[0]["category"] == "missing_tests"


class TestGapAnalyzerFindsMissingTests:
    def test_gap_analyzer_finds_missing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            src_dir = os.path.join(repo_root, "src", "pkg")
            os.makedirs(src_dir)
            impl_path = os.path.join(src_dir, "service.py")
            with open(impl_path, "w") as f:
                f.write("def do_stuff(): pass\n")
            test_dir = os.path.join(repo_root, "tests")
            os.makedirs(test_dir)
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint0", repo_root=repo_root)
            assert isinstance(report, GapReport)
            categories = [g.category for g in report.gaps]
            assert "missing_tests" in categories


class TestGapAnalyzerFindsMissingMoleculeScenarios:
    def test_gap_analyzer_finds_missing_molecule_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            pb_dir = os.path.join(repo_root, "playbooks")
            os.makedirs(pb_dir)
            pb_path = os.path.join(pb_dir, "deploy.yml")
            with open(pb_path, "w") as f:
                f.write("---\n- hosts: localhost\n  tasks: []\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint0", repo_root=repo_root)
            categories = [g.category for g in report.gaps]
            assert "missing_molecule" in categories


class TestGapAnalyzerEmptyRepoNoGaps:
    def test_gap_analyzer_empty_repo_no_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as repo_root:
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint0", repo_root=repo_root)
            assert isinstance(report, GapReport)
            assert report.total_gaps == 0


class TestLogAuditorDetectsMissingCorrelationIds:
    def test_log_auditor_detects_missing_correlation_ids(self) -> None:
        entries = [
            {"event": "task_start", "todo_id": "TODO-001"},
            {"event": "task_end", "todo_id": "TODO-002"},
        ]
        auditor = LogAuditor()
        report = auditor.audit_logs(entries)
        assert isinstance(report, AuditReport)
        categories = [f.category for f in report.findings]
        assert "missing_correlation_id" in categories


class TestLogAuditorDetectsSecretLikeValues:
    def test_log_auditor_detects_secret_like_values(self) -> None:
        entries = [
            {
                "event": "api_call",
                "correlation_id": "corr-1",
                "payload": {"api_key": "sk-abc123secretkey456xyz000"},
            },
        ]
        auditor = LogAuditor()
        report = auditor.audit_logs(entries)
        categories = [f.category for f in report.findings]
        assert "secret_like_value" in categories


class TestLogAuditorDetectsStuckTodos:
    def test_log_auditor_detects_stuck_todos(self) -> None:
        entries = [
            {
                "event": "status_change",
                "correlation_id": "corr-1",
                "todo_id": "TODO-STUCK",
                "from_status": "active",
                "to_status": "active",
                "attempt": 6,
            },
        ]
        auditor = LogAuditor()
        report = auditor.audit_logs(entries)
        categories = [f.category for f in report.findings]
        assert "stuck_todo" in categories


class TestLogAuditorCleanLogsNoFindings:
    def test_log_auditor_clean_logs_no_findings(self) -> None:
        entries = [
            {"event": "task_start", "correlation_id": "corr-1"},
            {"event": "task_end", "correlation_id": "corr-1"},
        ]
        auditor = LogAuditor()
        report = auditor.audit_logs(entries)
        assert report.total_findings == 0


class TestPlaybooksExist:
    def test_validate_task_playbook_exists(self) -> None:
        assert os.path.isfile(os.path.join(PLAYBOOK_DIR, "validate_task.yml"))

    def test_gap_analysis_playbook_exists(self) -> None:
        assert os.path.isfile(os.path.join(PLAYBOOK_DIR, "gap_analysis.yml"))

    def test_log_audit_playbook_exists(self) -> None:
        assert os.path.isfile(os.path.join(PLAYBOOK_DIR, "log_audit.yml"))
