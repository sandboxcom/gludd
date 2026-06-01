"""E2E: Objective 13 — Validation runner, gap analyzer, and log auditor."""

from __future__ import annotations

import os
import tempfile

from general_ludd.validation.gap_analyzer import GapAnalyzer, GapItem, GapReport
from general_ludd.validation.log_auditor import AuditFinding, AuditReport, LogAuditor
from general_ludd.validation.runner import ValidationResult, ValidationRunner


class TestValidationRunnerImportAndInit:
    def test_import_and_instantiation(self):
        runner = ValidationRunner(
            todo_id="TODO-001",
            worktree_path="/tmp/worktree",
            test_commands=["echo ok"],
        )
        assert runner.todo_id == "TODO-001"
        assert runner.worktree_path == "/tmp/worktree"
        assert runner.test_commands == ["echo ok"]

    def test_default_fields(self):
        runner = ValidationRunner(
            todo_id="TODO-002",
            worktree_path="/tmp",
            test_commands=[],
        )
        assert runner.test_commands == []


class TestValidationRunnerExecution:
    def test_run_validation_with_passing_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ValidationRunner(
                todo_id="TODO-010",
                worktree_path=tmpdir,
                test_commands=["echo '1 passed'"],
            )
            result = runner.run_validation()
            assert isinstance(result, ValidationResult)

    def test_create_child_todos_for_failures_empty(self):
        runner = ValidationRunner(todo_id="TODO-011", worktree_path="/tmp", test_commands=[])
        result = ValidationResult(success=True, passed_count=5, failed_count=0, output="ok")
        children = runner.create_child_todos_for_failures(result)
        assert children == []

    def test_create_child_todos_with_failures(self):
        runner = ValidationRunner(todo_id="TODO-012", worktree_path="/tmp", test_commands=[])
        result = ValidationResult(
            success=False,
            passed_count=3,
            failed_count=2,
            output="fail",
            failures=["tests/test_foo.py::test_bar", "tests/test_baz.py::test_qux"],
        )
        children = runner.create_child_todos_for_failures(result)
        assert len(children) == 2
        assert children[0]["parent_todo_id"] == "TODO-012"
        assert children[0]["category"] == "test_failure"

    def test_create_child_todos_no_tests_found(self):
        runner = ValidationRunner(todo_id="TODO-013", worktree_path="/tmp", test_commands=[])
        result = ValidationResult(success=False, passed_count=0, failed_count=0, output="")
        children = runner.create_child_todos_for_failures(result)
        assert len(children) == 1
        assert children[0]["category"] == "missing_tests"

    def test_validation_result_dataclass(self):
        result = ValidationResult(
            success=True,
            passed_count=10,
            failed_count=0,
            output="all good",
            failures=[],
        )
        assert result.success
        assert result.passed_count == 10
        assert result.failed_count == 0


class TestGapAnalyzerImportAndDetection:
    def test_import_and_instantiation(self):
        analyzer = GapAnalyzer()
        assert analyzer is not None

    def test_analyze_empty_repo(self):
        analyzer = GapAnalyzer()
        with tempfile.TemporaryDirectory() as tmpdir:
            report = analyzer.analyze(sprint_path="sprint0", repo_root=tmpdir)
            assert isinstance(report, GapReport)
            assert report.total_gaps == 0

    def test_analyze_finds_missing_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src", "pkg")
            os.makedirs(src_dir)
            with open(os.path.join(src_dir, "feature.py"), "w") as f:
                f.write("x = 1\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint0", repo_root=tmpdir)
            assert report.total_gaps >= 1
            categories = [g.category for g in report.gaps]
            assert "missing_tests" in categories

    def test_analyze_no_gap_when_test_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src", "pkg")
            test_dir = os.path.join(tmpdir, "tests")
            os.makedirs(src_dir)
            os.makedirs(test_dir)
            with open(os.path.join(src_dir, "feature.py"), "w") as f:
                f.write("x = 1\n")
            with open(os.path.join(test_dir, "test_feature.py"), "w") as f:
                f.write("def test_feature(): pass\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint0", repo_root=tmpdir)
            missing = [g for g in report.gaps if g.category == "missing_tests" and "feature.py" in g.description]
            assert len(missing) == 0

    def test_gap_item_fields(self):
        item = GapItem(
            category="missing_tests",
            description="impl has no test",
            severity="medium",
            suggested_action="write test",
        )
        assert item.category == "missing_tests"
        assert item.severity == "medium"

    def test_gap_report_dataclass(self):
        report = GapReport(total_gaps=0, gaps=[])
        assert report.total_gaps == 0
        assert report.gaps == []

    def test_analyze_finds_missing_molecule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pb_dir = os.path.join(tmpdir, "playbooks")
            os.makedirs(pb_dir)
            with open(os.path.join(pb_dir, "deploy.yml"), "w") as f:
                f.write("---\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint0", repo_root=tmpdir)
            mol_gaps = [g for g in report.gaps if g.category == "missing_molecule"]
            assert len(mol_gaps) >= 1
            assert mol_gaps[0].severity == "high"


class TestLogAuditorImportAndFindingDetection:
    def test_import_and_instantiation(self):
        auditor = LogAuditor()
        assert auditor is not None

    def test_audit_clean_logs(self):
        auditor = LogAuditor()
        entries = [
            {"event": "task_complete", "correlation_id": "corr-001", "todo_id": "T-001"},
            {"event": "task_dispatch", "correlation_id": "corr-002", "todo_id": "T-002"},
        ]
        report = auditor.audit_logs(entries)
        assert isinstance(report, AuditReport)
        assert report.total_findings == 0

    def test_audit_detects_missing_correlation_id(self):
        auditor = LogAuditor()
        entries = [{"event": "task_complete", "todo_id": "T-001"}]
        report = auditor.audit_logs(entries)
        assert report.total_findings >= 1
        categories = [f.category for f in report.findings]
        assert "missing_correlation_id" in categories

    def test_audit_detects_secret_like_value(self):
        auditor = LogAuditor()
        entries = [
            {
                "event": "config_load",
                "correlation_id": "corr-003",
                "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",
            }
        ]
        report = auditor.audit_logs(entries)
        assert report.total_findings >= 1
        categories = [f.category for f in report.findings]
        assert "secret_like_value" in categories
        secret_findings = [f for f in report.findings if f.category == "secret_like_value"]
        assert secret_findings[0].severity == "critical"

    def test_audit_detects_stuck_todo(self):
        auditor = LogAuditor()
        entries = [
            {
                "event": "status_change",
                "correlation_id": "corr-004",
                "todo_id": "T-999",
                "attempt": 5,
                "from_status": "active",
                "to_status": "active",
            }
        ]
        report = auditor.audit_logs(entries)
        assert report.total_findings >= 1
        categories = [f.category for f in report.findings]
        assert "stuck_todo" in categories
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert stuck[0].severity == "high"

    def test_audit_no_stuck_when_attempt_below_threshold(self):
        auditor = LogAuditor()
        entries = [
            {
                "event": "status_change",
                "correlation_id": "corr-005",
                "todo_id": "T-100",
                "attempt": 3,
                "from_status": "active",
                "to_status": "active",
            }
        ]
        report = auditor.audit_logs(entries)
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) == 0

    def test_audit_detects_ghp_token(self):
        auditor = LogAuditor()
        entries = [
            {
                "event": "push",
                "correlation_id": "corr-006",
                "token": "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            }
        ]
        report = auditor.audit_logs(entries)
        categories = [f.category for f in report.findings]
        assert "secret_like_value" in categories

    def test_audit_empty_entries(self):
        auditor = LogAuditor()
        report = auditor.audit_logs([])
        assert report.total_findings == 0
        assert report.findings == []

    def test_audit_finding_dataclass(self):
        finding = AuditFinding(
            severity="critical",
            category="secret_like_value",
            description="secret detected",
            evidence="sk-abc...",
        )
        assert finding.severity == "critical"
        assert finding.category == "secret_like_value"


class TestPlaybookStubsForValidation:
    def test_validate_task_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "playbooks", "validate_task.yml"))

    def test_gap_analysis_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "playbooks", "gap_analysis.yml"))

    def test_log_audit_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        assert os.path.exists(os.path.join(repo_root, "playbooks", "log_audit.yml"))
