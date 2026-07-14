"""Structural tests for project_runner/findings.py — SAST parsers."""

from __future__ import annotations

import json

from general_ludd.project_runner.findings import (
    parse_findings,
    summarize_findings,
)


class TestParseFindings:
    def test_empty_string(self):
        assert parse_findings("semgrep", "") == []

    def test_non_json_input(self):
        assert parse_findings("semgrep", "not json") == []

    def test_empty_json_object(self):
        assert parse_findings("semgrep", "{}") == []

    def test_unknown_tool_returns_empty(self):
        assert parse_findings("unknown-tool", '{"results": []}') == []

    def test_semgrep_empty_results(self):
        assert parse_findings("semgrep", '{"results": []}') == []

    def test_semgrep_one_finding(self):
        doc = {
            "results": [
                {
                    "check_id": "python.lang.security",
                    "path": "src/app.py",
                    "start": {"line": 42},
                    "extra": {"severity": "ERROR", "message": "unsafe deserialization"},
                }
            ]
        }
        findings = parse_findings("semgrep", json.dumps(doc))
        assert len(findings) == 1
        assert "ERROR" in findings[0]
        assert "src/app.py:42" in findings[0]
        assert "python.lang.security" in findings[0]
        assert "unsafe deserialization" in findings[0]

    def test_semgrep_missing_start(self):
        doc = {
            "results": [
                {
                    "check_id": "R001",
                    "path": "src/x.py",
                    "extra": {"severity": "WARNING", "message": "msg"},
                }
            ]
        }
        findings = parse_findings("semgrep", json.dumps(doc))
        assert len(findings) == 1
        assert ":?" in findings[0]

    def test_bandit_one_finding(self):
        doc = {
            "results": [
                {
                    "test_id": "B101",
                    "filename": "src/main.py",
                    "line_number": 10,
                    "issue_severity": "MEDIUM",
                    "issue_text": "use of exec",
                }
            ]
        }
        findings = parse_findings("bandit", json.dumps(doc))
        assert len(findings) == 1
        assert "MEDIUM" in findings[0]
        assert "src/main.py:10" in findings[0]
        assert "B101" in findings[0]

    def test_eslint_one_finding(self):
        doc = [
            {
                "filePath": "src/index.js",
                "messages": [
                    {"ruleId": "no-unused-vars", "severity": 2, "line": 5, "message": "x is defined but never used"}
                ],
            }
        ]
        findings = parse_findings("eslint", json.dumps(doc))
        assert len(findings) == 1
        assert "ERROR" in findings[0]
        assert "src/index.js:5" in findings[0]

    def test_eslint_warning_severity(self):
        doc = [
            {
                "filePath": "app.js",
                "messages": [
                    {"ruleId": "no-console", "severity": 1, "line": 3, "message": "unexpected console"}
                ],
            }
        ]
        findings = parse_findings("eslint", json.dumps(doc))
        assert len(findings) == 1
        assert "WARNING" in findings[0]

    def test_golangci_lint_one_finding(self):
        doc = {
            "Issues": [
                {
                    "FromLinter": "errcheck",
                    "Pos": {"Filename": "main.go", "Line": 15},
                    "Severity": "error",
                    "Text": "error not checked",
                }
            ]
        }
        findings = parse_findings("golangci-lint", json.dumps(doc))
        assert len(findings) == 1
        assert "ERROR" in findings[0]
        assert "main.go:15" in findings[0]
        assert "errcheck" in findings[0]

    def test_cargo_audit_one_finding(self):
        doc = {
            "vulnerabilities": {
                "list": [
                    {
                        "advisory": {"id": "RUSTSEC-2021-0073", "title": "Buffer overflow", "cvss": "7.5"},
                        "package": {"name": "hyper", "version": "0.14.0"},
                    }
                ]
            }
        }
        findings = parse_findings("cargo-audit", json.dumps(doc))
        assert len(findings) == 1
        assert "HIGH" in findings[0]
        assert "hyper@0.14.0" in findings[0]

    def test_trivy_one_vuln(self):
        doc = {
            "Results": [
                {
                    "Target": "app.py",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2023-1234",
                            "Severity": "CRITICAL",
                            "PkgName": "flask",
                            "Title": "RCE in flask",
                        }
                    ],
                }
            ]
        }
        findings = parse_findings("trivy", json.dumps(doc))
        assert len(findings) == 1
        assert "CRITICAL" in findings[0]
        assert "CVE-2023-1234" in findings[0]

    def test_trivy_misconfiguration(self):
        doc = {
            "Results": [
                {
                    "Target": "Dockerfile",
                    "Misconfigurations": [
                        {
                            "ID": "DS002",
                            "Severity": "HIGH",
                            "Message": "root user",
                            "CauseMetadata": {"StartLine": 1},
                        }
                    ],
                }
            ]
        }
        findings = parse_findings("trivy", json.dumps(doc))
        assert len(findings) == 1
        assert "HIGH" in findings[0]
        assert "Dockerfile:1" in findings[0]

    def test_parser_exceptions_are_suppressed(self):
        doc = {"results": [None]}  # None is not iterable for .get()
        findings = parse_findings("semgrep", json.dumps(doc))
        assert findings == [] or all(isinstance(f, str) for f in findings)


class TestSummarizeFindings:
    def test_empty_list(self):
        assert summarize_findings([]) == {}

    def test_single_high(self):
        result = summarize_findings(["HIGH src/x.py:1 R001 — msg"])
        assert result == {"HIGH": 1}

    def test_multiple_severities(self):
        findings = [
            "HIGH src/a.py:1 R001 — msg",
            "MEDIUM src/b.py:2 R002 — msg",
            "HIGH src/c.py:3 R003 — msg",
            "LOW src/d.py:4 R004 — msg",
        ]
        result = summarize_findings(findings)
        assert result == {"HIGH": 2, "MEDIUM": 1, "LOW": 1}

    def test_empty_string_element(self):
        result = summarize_findings([""])
        assert result == {"UNKNOWN": 1}
