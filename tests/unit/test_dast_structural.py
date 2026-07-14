"""Structural tests for project_runner/dast.py — DastConfig, DastFinding, DastResult, parsers, validators."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from general_ludd.project_runner.dast import (
    DastConfig,
    DastFinding,
    DastResult,
    _is_blocked_target,
    _is_loopback,
    _severity_exceeds,
    _validate_target_url,
    is_blocked_target,
    is_loopback,
    parse_zap_baseline,
    severity_threshold_exceeded,
)


class TestDastConfig:
    def test_target_url_only_valid(self):
        cfg = DastConfig(target_url="https://example.com")
        assert cfg.target_url == "https://example.com"
        assert cfg.start_command is None
        assert cfg.port is None

    def test_start_command_with_port_valid(self):
        cfg = DastConfig(start_command="python app.py", port=8080)
        assert cfg.start_command == "python app.py"
        assert cfg.port == 8080

    def test_both_start_command_and_target_url_raises(self):
        with pytest.raises(ValidationError):
            DastConfig(start_command="python app.py", port=8080, target_url="https://example.com")

    def test_neither_raises(self):
        with pytest.raises(ValidationError):
            DastConfig()

    def test_start_command_without_port_raises(self):
        with pytest.raises(ValidationError):
            DastConfig(start_command="python app.py")

    def test_invalid_fail_on_raises(self):
        with pytest.raises(ValidationError):
            DastConfig(target_url="https://example.com", fail_on="CRITICAL")

    def test_valid_fail_on_values(self):
        for val in ("HIGH", "MEDIUM", "LOW"):
            cfg = DastConfig(target_url="https://e.com", fail_on=val)
            assert cfg.fail_on == val

    def test_fail_on_case_insensitive(self):
        cfg = DastConfig(target_url="https://e.com", fail_on="high")
        assert cfg.fail_on == "HIGH"

    def test_defaults(self):
        cfg = DastConfig(target_url="https://e.com")
        assert cfg.health_path == "/"
        assert cfg.startup_timeout_s == 60
        assert cfg.tool == "zap-baseline.py"
        assert cfg.max_duration_s == 900
        assert cfg.fail_on == "HIGH"


class TestDastFinding:
    def test_default_fields(self):
        f = DastFinding(
            severity="HIGH", rule_id="R001", url="http://x", method="GET", evidence="vuln", solution="fix"
        )
        assert f.cwe_id == ""
        assert f.riskcode == 0

    def test_explicit_fields(self):
        f = DastFinding(
            severity="MEDIUM",
            rule_id="R002",
            url="http://y",
            method="POST",
            evidence="data",
            solution="patch",
            cwe_id="CWE-79",
            riskcode=2,
        )
        assert f.cwe_id == "CWE-79"
        assert f.riskcode == 2


class TestDastResult:
    def test_defaults(self):
        r = DastResult(passed=True)
        assert r.passed is True
        assert r.skipped is False
        assert r.reason is None
        assert r.findings == []

    def test_with_findings(self):
        f = DastFinding(severity="HIGH", rule_id="R", url="/", method="GET", evidence="e", solution="s")
        r = DastResult(passed=False, findings=[f])
        assert len(r.findings) == 1

    def test_skipped_result(self):
        r = DastResult(passed=False, skipped=True, reason="tool not found")
        assert r.skipped is True
        assert r.reason == "tool not found"


class TestIsLoopback:
    def test_ipv4_127_0_0_1(self):
        assert _is_loopback("127.0.0.1") is True

    def test_ipv4_127_255_255_255(self):
        assert _is_loopback("127.255.255.255") is True

    def test_ipv4_localhost_hostname(self):
        assert _is_loopback("localhost") is True

    def test_ipv6_loopback(self):
        assert _is_loopback("::1") is True

    def test_external_ip(self):
        assert _is_loopback("93.184.216.34") is False

    def test_non_ip_localhost_subdomain(self):
        assert _is_loopback("api.localhost") is True

    def test_public_alias(self):
        assert is_loopback is _is_loopback


class TestIsBlockedTarget:
    def test_private_10(self):
        assert _is_blocked_target("10.0.0.1") is True

    def test_private_172_16(self):
        assert _is_blocked_target("172.16.0.1") is True

    def test_private_192_168(self):
        assert _is_blocked_target("192.168.1.1") is True

    def test_link_local(self):
        assert _is_blocked_target("169.254.1.1") is True

    def test_metadata_ip(self):
        assert _is_blocked_target("169.254.169.254") is True

    def test_public_ip_not_blocked(self):
        assert _is_blocked_target("93.184.216.34") is False

    def test_hostname_not_blocked(self):
        assert _is_blocked_target("example.com") is False

    def test_public_alias(self):
        assert is_blocked_target is _is_blocked_target


class TestValidateTargetUrl:
    def test_loopback_url_passes(self):
        _validate_target_url("http://127.0.0.1:8080/health")

    def test_blocked_private_range_raises(self):
        with pytest.raises(ValueError, match="blocked range"):
            _validate_target_url("http://10.0.0.5:3000")

    def test_malformed_url_raises(self):
        with pytest.raises(ValueError):
            _validate_target_url("not-a-url")


class TestParseZapBaseline:
    def test_empty_string(self):
        assert parse_zap_baseline("") == []

    def test_non_json(self):
        assert parse_zap_baseline("not json") == []

    def test_empty_json_object(self):
        assert parse_zap_baseline("{}") == []

    def test_non_dict_json(self):
        assert parse_zap_baseline("[]") == []

    def test_single_finding(self):
        doc = {
            "site": [
                {
                    "alerts": [
                        {
                            "alert": "XSS",
                            "riskcode": "3",
                            "cweid": "79",
                            "desc": "Cross-site scripting",
                            "solution": "Sanitize input",
                            "instances": [
                                {"uri": "http://target/page", "method": "GET", "evidence": "<script>"}
                            ],
                        }
                    ]
                }
            ]
        }
        findings = parse_zap_baseline(json.dumps(doc))
        assert len(findings) == 1
        assert findings[0].severity == "HIGH"
        assert findings[0].rule_id == "XSS"
        assert findings[0].url == "http://target/page"
        assert findings[0].method == "GET"

    def test_finding_deduplication(self):
        doc = {
            "site": [
                {
                    "alerts": [
                        {
                            "alert": "SQLi",
                            "riskcode": "3",
                            "instances": [
                                {"uri": "/x", "method": "GET", "evidence": "e"},
                                {"uri": "/x", "method": "GET", "evidence": "e2"},
                            ],
                        }
                    ]
                }
            ]
        }
        findings = parse_zap_baseline(json.dumps(doc))
        assert len(findings) == 1


class TestSeverityExceeds:
    def test_no_findings(self):
        assert _severity_exceeds([], "HIGH") is False

    def test_finding_at_threshold(self):
        f = DastFinding(severity="HIGH", rule_id="R", url="/", method="GET", evidence="e", solution="s")
        assert _severity_exceeds([f], "HIGH") is True

    def test_finding_below_threshold(self):
        f = DastFinding(severity="MEDIUM", rule_id="R", url="/", method="GET", evidence="e", solution="s")
        assert _severity_exceeds([f], "HIGH") is False

    def test_finding_above_threshold(self):
        f = DastFinding(severity="HIGH", rule_id="R", url="/", method="GET", evidence="e", solution="s")
        assert _severity_exceeds([f], "MEDIUM") is True

    def test_public_alias(self):
        assert severity_threshold_exceeded is _severity_exceeds
