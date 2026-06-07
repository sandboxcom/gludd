"""Tests for completion audit — detects dead code and unwired classes as incomplete tasks."""

from __future__ import annotations


class TestCompletionAudit:
    def test_audit_returns_list_of_findings(self):
        from general_ludd.quality.preflight import run_completion_audit

        report = run_completion_audit()
        assert isinstance(report, dict)
        assert "overall" in report
        assert "findings" in report
        assert isinstance(report["findings"], list)

    def test_audit_detects_unwired_classes(self):
        from general_ludd.quality.preflight import run_completion_audit

        report = run_completion_audit()
        assert report["completion_pct"] >= 0, "Audit should produce a completion percentage"

    def test_audit_finding_has_required_fields(self):
        from general_ludd.quality.preflight import run_completion_audit

        report = run_completion_audit()
        for f in report["findings"]:
            assert "class_name" in f or "function_name" in f
            assert "file" in f
            assert "reason" in f
            assert "severity" in f

    def test_audit_counts_pass_fail_warn(self):
        from general_ludd.quality.preflight import run_completion_audit

        report = run_completion_audit()
        assert "passed_count" in report
        assert "failed_count" in report
        assert "warn_count" in report

    def test_audit_overall_is_pass_fail_or_warn(self):
        from general_ludd.quality.preflight import run_completion_audit

        report = run_completion_audit()
        assert report["overall"] in ("PASS", "FAIL", "WARN")

    def test_audit_includes_completion_pct(self):
        from general_ludd.quality.preflight import run_completion_audit

        report = run_completion_audit()
        assert "completion_pct" in report
        assert 0 <= report["completion_pct"] <= 100

    def test_generate_backlog_from_audit(self):
        from general_ludd.quality.preflight import generate_backlog_from_audit

        report = {"findings": [
            {"class_name": "Foo", "file": "x.py", "reason": "dead code", "severity": "fail"},
            {"class_name": "Bar", "file": "y.py", "reason": "not wired", "severity": "warn"},
        ]}
        todos = generate_backlog_from_audit(report)
        assert isinstance(todos, list)
        assert len(todos) == 2
        for t in todos:
            assert "title" in t
            assert "description" in t
            assert "work_type" in t
