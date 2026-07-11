"""Tests for eslint / golangci-lint / cargo-audit / trivy JSON parsers."""

from __future__ import annotations

import json

from general_ludd.project_runner.findings import parse_findings, summarize_findings

ESLINT_JSON = json.dumps(
    [
        {
            "filePath": "src/app.js",
            "messages": [
                {
                    "ruleId": "no-unused-vars",
                    "severity": 2,
                    "line": 10,
                    "column": 5,
                    "message": "'x' is assigned but never used.",
                },
                {
                    "ruleId": "semi",
                    "severity": 1,
                    "line": 15,
                    "column": 1,
                    "message": "Missing semicolon.",
                },
            ],
        },
        {
            "filePath": "src/util.js",
            "messages": [
                {
                    "ruleId": "no-console",
                    "severity": 2,
                    "line": 3,
                    "column": 1,
                    "message": "Unexpected console statement.",
                }
            ],
        },
    ]
)

GOLANGCI_JSON = json.dumps(
    {
        "Issues": [
            {
                "FromLinter": "govet",
                "Text": "printf: wrong number of args in Errorf call",
                "Pos": {"Filename": "main.go", "Offset": 0, "Line": 42, "Column": 10},
                "Severity": "error",
            },
            {
                "FromLinter": "errcheck",
                "Text": "Error return value not checked",
                "Pos": {"Filename": "pkg/handler.go", "Offset": 120, "Line": 77, "Column": 3},
            },
        ]
    }
)

CARGO_AUDIT_JSON = json.dumps(
    {
        "vulnerabilities": {
            "list": [
                {
                    "advisory": {
                        "id": "RUSTSEC-2024-0001",
                        "title": "Buffer overflow in parser",
                        "cvss": "9.8",
                    },
                    "package": {"name": "some-crate", "version": "1.0.0"},
                },
                {
                    "advisory": {
                        "id": "RUSTSEC-2024-0002",
                        "title": "Information leak via timing side-channel",
                        "cvss": "2.3",
                    },
                    "package": {"name": "other-crate", "version": "0.5.1"},
                },
                {
                    "advisory": {
                        "id": "RUSTSEC-2025-0003",
                        "title": "Unknown severity advisory",
                    },
                    "package": {"name": "no-sev-crate", "version": "2.0.0"},
                },
            ]
        }
    }
)

TRIVY_JSON = json.dumps(
    {
        "Results": [
            {
                "Target": "app/package.json",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2023-1234",
                        "Title": "Prototype Pollution in merge()",
                        "Severity": "HIGH",
                        "PkgName": "lodash",
                        "InstalledVersion": "4.17.20",
                    },
                ],
                "Misconfigurations": [
                    {
                        "ID": "AVD-KSV-001",
                        "Title": "Pod running with host network",
                        "Severity": "MEDIUM",
                        "Message": "Container should not enable hostNetwork",
                        "CauseMetadata": {"StartLine": 12, "EndLine": 14},
                    }
                ],
            },
            {
                "Target": "Dockerfile",
                "Vulnerabilities": [],
                "Misconfigurations": [
                    {
                        "ID": "DS026",
                        "Title": "Image runs as root",
                        "Severity": "CRITICAL",
                        "Message": "Last USER is root or not set",
                        "CauseMetadata": {"StartLine": 1, "EndLine": 1},
                    }
                ],
            },
        ]
    }
)


class TestEslintParser:
    def test_parses_findings(self):
        f = parse_findings("eslint", ESLINT_JSON)
        assert len(f) == 3
        assert any("ERROR" in x and "no-unused-vars" in x for x in f)
        assert any("ERROR" in x and "no-console" in x for x in f)
        assert any("WARNING" in x and "semi" in x for x in f)

    def test_severity_maps_error_to_error(self):
        f = parse_findings("eslint", ESLINT_JSON)
        no_unused = next(x for x in f if "no-unused-vars" in x)
        assert no_unused.startswith("ERROR")

    def test_severity_maps_warning_to_warning(self):
        f = parse_findings("eslint", ESLINT_JSON)
        semi = next(x for x in f if "semi" in x)
        assert semi.startswith("WARNING")

    def test_returns_empty_for_non_list(self):
        assert parse_findings("eslint", "{}") == []
        assert parse_findings("eslint", "null") == []

    def test_returns_empty_for_invalid_json(self):
        assert parse_findings("eslint", "[{bad json]") == []

    def test_empty_array_returns_empty(self):
        assert parse_findings("eslint", "[]") == []


class TestGolangciLintParser:
    def test_parses_findings(self):
        f = parse_findings("golangci-lint", GOLANGCI_JSON)
        assert len(f) == 2
        assert any("main.go:42" in x and "govet" in x for x in f)
        assert any("pkg/handler.go:77" in x and "errcheck" in x for x in f)

    def test_severity_present(self):
        f = parse_findings("golangci-lint", GOLANGCI_JSON)
        govet = next(x for x in f if "govet" in x)
        assert govet.startswith("ERROR")

    def test_missing_severity_defaults_to_warning(self):
        f = parse_findings("golangci-lint", GOLANGCI_JSON)
        errcheck = next(x for x in f if "errcheck" in x)
        assert "WARNING" in errcheck.split(" ")[0]

    def test_empty_issues_returns_empty(self):
        assert parse_findings("golangci-lint", '{"Issues": []}') == []

    def test_non_dict_returns_empty(self):
        assert parse_findings("golangci-lint", "[]") == []


class TestCargoAuditParser:
    def test_parses_vulnerabilities(self):
        f = parse_findings("cargo-audit", CARGO_AUDIT_JSON)
        assert len(f) == 3
        assert any("some-crate" in x and "RUSTSEC-2024-0001" in x for x in f)
        assert any("other-crate" in x and "RUSTSEC-2024-0002" in x for x in f)
        assert any("no-sev-crate" in x and "RUSTSEC-2025-0003" in x for x in f)

    def test_cvss_score_critical_maps_to_critical(self):
        f = parse_findings("cargo-audit", CARGO_AUDIT_JSON)
        crit = next(x for x in f if "RUSTSEC-2024-0001" in x)
        assert crit.startswith("CRITICAL")

    def test_cvss_score_low_maps_to_low(self):
        f = parse_findings("cargo-audit", CARGO_AUDIT_JSON)
        low = next(x for x in f if "RUSTSEC-2024-0002" in x)
        assert low.startswith("LOW")

    def test_missing_cvss_defaults_to_unknown(self):
        f = parse_findings("cargo-audit", CARGO_AUDIT_JSON)
        unk = next(x for x in f if "RUSTSEC-2025-0003" in x)
        assert unk.startswith("UNKNOWN")

    def test_no_vulnerabilities_returns_empty(self):
        assert parse_findings("cargo-audit", '{"vulnerabilities": {"list": []}}') == []

    def test_non_dict_returns_empty(self):
        assert parse_findings("cargo-audit", "[]") == []


class TestTrivyParser:
    def test_parses_vulnerabilities_and_misconfigurations(self):
        f = parse_findings("trivy", TRIVY_JSON)
        assert len(f) == 3

    def test_vulnerability_parsed(self):
        f = parse_findings("trivy", TRIVY_JSON)
        vuln = next(x for x in f if "CVE-2023-1234" in x)
        assert vuln.startswith("HIGH")
        assert "lodash" in vuln
        assert "Prototype Pollution" in vuln
        assert "app/package.json" in vuln

    def test_misconfiguration_parsed(self):
        f = parse_findings("trivy", TRIVY_JSON)
        misc = next(x for x in f if "AVD-KSV-001" in x)
        assert misc.startswith("MEDIUM")
        assert ":12" in misc

    def test_misconfiguration_without_cause_metadata(self):
        doc = json.dumps(
            {
                "Results": [
                    {
                        "Target": ".",
                        "Vulnerabilities": [],
                        "Misconfigurations": [
                            {
                                "ID": "TEST-001",
                                "Title": "No metadata test",
                                "Severity": "LOW",
                                "Message": "Description text",
                            }
                        ],
                    }
                ]
            }
        )
        f = parse_findings("trivy", doc)
        assert len(f) == 1
        assert ":?" in f[0]

    def test_empty_results_returns_empty(self):
        assert parse_findings("trivy", '{"Results": []}') == []

    def test_non_dict_returns_empty(self):
        assert parse_findings("trivy", "[]") == []


class TestSummarizeNewParsers:
    def test_summarize_eslint(self):
        f = parse_findings("eslint", ESLINT_JSON)
        counts = summarize_findings(f)
        assert counts == {"ERROR": 2, "WARNING": 1}

    def test_summarize_trivy(self):
        f = parse_findings("trivy", TRIVY_JSON)
        counts = summarize_findings(f)
        assert counts == {"HIGH": 1, "MEDIUM": 1, "CRITICAL": 1}

    def test_summarize_cargo_audit(self):
        f = parse_findings("cargo-audit", CARGO_AUDIT_JSON)
        counts = summarize_findings(f)
        assert counts["CRITICAL"] == 1
        assert counts["LOW"] == 1
        assert counts["UNKNOWN"] == 1

    def test_cvss_vector_returns_unknown(self):
        doc = json.dumps(
            {
                "vulnerabilities": {
                    "list": [
                        {
                            "advisory": {
                                "id": "RUSTSEC-2026-0001",
                                "title": "Vector only",
                                "cvss": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                            },
                            "package": {"name": "vec-crate", "version": "1.0"},
                        }
                    ]
                }
            }
        )
        f = parse_findings("cargo-audit", doc)
        assert len(f) == 1
        assert f[0].startswith("UNKNOWN")


class TestUnknownToolStillEmpty:
    def test_unknown_returns_empty(self):
        assert parse_findings("pytest", ESLINT_JSON) == []
        assert parse_findings("", GOLANGCI_JSON) == []
