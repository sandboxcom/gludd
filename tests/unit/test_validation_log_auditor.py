"""Unit tests for validation/log_auditor.py — log entry anomaly detection."""

from __future__ import annotations

from general_ludd.validation.log_auditor import (
    AuditFinding,
    AuditReport,
    LogAuditor,
)


class TestAuditFinding:
    def test_construction(self) -> None:
        finding = AuditFinding(
            severity="high",
            category="stuck_todo",
            description="Todo appears stuck",
            evidence='{"todo_id": "123"}',
        )
        assert finding.severity == "high"
        assert finding.category == "stuck_todo"

    def test_critical_finding(self) -> None:
        finding = AuditFinding(
            severity="critical",
            category="secret_like_value",
            description="Found secret-like value",
            evidence="sk-abcdefghijklmnopqrstuvwx",
        )
        assert finding.severity == "critical"


class TestAuditReport:
    def test_empty_report(self) -> None:
        report = AuditReport()
        assert report.total_findings == 0
        assert report.findings == []

    def test_report_with_findings(self) -> None:
        findings = [
            AuditFinding(severity="low", category="c1", description="d1", evidence="e1"),
        ]
        report = AuditReport(findings=findings, total_findings=1)
        assert report.total_findings == 1
        assert len(report.findings) == 1


class TestLogAuditorAuditLogs:
    def test_empty_logs_returns_no_findings(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([])
        assert report.total_findings == 0

    def test_missing_correlation_id_flagged(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([{"event": "task_complete", "todo_id": "42"}])
        missing_cid = [
            f for f in report.findings if f.category == "missing_correlation_id"
        ]
        assert len(missing_cid) >= 1

    def test_valid_entry_with_correlation_id_passes(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {"event": "task_complete", "correlation_id": "abc-123", "todo_id": "42"}
        ])
        missing_cid = [
            f for f in report.findings if f.category == "missing_correlation_id"
        ]
        assert len(missing_cid) == 0

    def test_stuck_todo_detected(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "retry",
                "correlation_id": "cid",
                "attempt": 7,
                "from_status": "in_progress",
                "to_status": "in_progress",
                "todo_id": "todo-1",
            }
        ])
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) >= 1
        assert stuck[0].severity == "high"

    def test_below_retry_threshold_not_flagged(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "retry",
                "correlation_id": "cid",
                "attempt": 3,
                "from_status": "in_progress",
                "to_status": "in_progress",
                "todo_id": "todo-1",
            }
        ])
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) == 0

    def test_status_change_above_threshold_not_stuck(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "retry",
                "correlation_id": "cid",
                "attempt": 8,
                "from_status": "in_progress",
                "to_status": "completed",
                "todo_id": "todo-1",
            }
        ])
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) == 0

    def test_secret_like_value_detected_in_payload(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "api_call",
                "correlation_id": "cid",
                "payload": {"api_key": "sk-abcdefghijklmnopqrstuvwxy"},
            }
        ])
        secret_findings = [
            f for f in report.findings if f.category == "secret_like_value"
        ]
        assert len(secret_findings) >= 1
        assert secret_findings[0].severity == "critical"

    def test_secret_like_value_detected_in_top_level_field(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "error",
                "correlation_id": "cid",
                "error_message": "Failed with key AKIA1234567890ABCDEF",
            }
        ])
        secret_findings = [
            f for f in report.findings if f.category == "secret_like_value"
        ]
        assert len(secret_findings) >= 1

    def test_clean_payload_no_secret_flagged(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "task_complete",
                "correlation_id": "cid",
                "payload": {"todo_id": "42", "status": "completed"},
            }
        ])
        secret_findings = [
            f for f in report.findings if f.category == "secret_like_value"
        ]
        assert len(secret_findings) == 0

    def test_non_dict_payload_handled(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "task_complete",
                "correlation_id": "cid",
                "payload": "not_a_dict",
            }
        ])
        assert report.total_findings == 0

    def test_attempt_non_int_handled(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "retry",
                "correlation_id": "cid",
                "attempt": "five",
                "todo_id": "todo-1",
            }
        ])
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) == 0

    def test_github_token_detected(self) -> None:
        auditor = LogAuditor()
        report = auditor.audit_logs([
            {
                "event": "push",
                "correlation_id": "cid",
                "token": "ghp_abcdefghijklmnopqrstuvwxyz123456",
            }
        ])
        secret_findings = [
            f for f in report.findings if f.category == "secret_like_value"
        ]
        assert len(secret_findings) >= 1
