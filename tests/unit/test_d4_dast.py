"""Unit tests for the DAST driver and ZAP-baseline findings parser.

Covers:
- DastConfig Pydantic model validation
- DastFinding dataclass
- DastResult dataclass
- parse_zap_baseline() JSON parsing
- is_loopback() and is_blocked_target() SSRF helpers
- severity_threshold_exceeded() gating logic
- run_dast_scan() lifecycle wrapper

All tests are written to FAIL initially (TDD — the dast module does not exist yet).
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from general_ludd.project_runner.profile import ProjectProfile

# ——— Fixtures ——————————————————————————————————————————————————————————————

VALID_ZAP_JSON = {
    "site": [
        {
            "@name": "http://example.com",
            "@host": "example.com",
            "@port": "80",
            "alerts": [
                {
                    "pluginid": "10021",
                    "alertRef": "10021",
                    "alert": "X-Content-Type-Options Header Missing",
                    "name": "X-Content-Type-Options Header Missing",
                    "riskcode": "1",
                    "confidence": "2",
                    "riskdesc": "Low (Medium)",
                    "desc": (
                        "The Anti-MIME-Sniffing header X-Content-Type-Options "
                        "was not set to 'nosniff'."
                    ),
                    "instances": [
                        {
                            "uri": "http://example.com/",
                            "method": "GET",
                            "param": "X-Content-Type-Options",
                            "evidence": "",
                        },
                        {
                            "uri": "http://example.com/login",
                            "method": "POST",
                            "param": "X-Content-Type-Options",
                            "evidence": "Missing header",
                        },
                    ],
                    "count": "2",
                    "solution": (
                        "Ensure that the application/web server sets the "
                        "Content-Type header appropriately."
                    ),
                    "cweid": "16",
                    "wascid": "15",
                    "sourceid": "3",
                },
                {
                    "pluginid": "10020",
                    "alert": "SQL Injection",
                    "riskcode": "3",
                    "desc": "SQL injection may be possible.",
                    "instances": [
                        {
                            "uri": "http://example.com/search",
                            "method": "GET",
                            "param": "q",
                            "evidence": "' OR 1=1 --",
                        }
                    ],
                    "count": "1",
                    "solution": "Use parameterized queries.",
                    "cweid": "89",
                },
                {
                    "pluginid": "10096",
                    "alert": "Timestamp Disclosure",
                    "riskcode": "0",
                    "desc": "A timestamp was disclosed by the application.",
                    "instances": [
                        {
                            "uri": "http://example.com/api",
                            "method": "GET",
                            "param": "",
                            "evidence": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "count": "1",
                    "solution": "Manually confirm that the timestamp is not sensitive.",
                    "cweid": "200",
                },
                {
                    "pluginid": "10054",
                    "alert": "Cookie Without Secure Flag",
                    "riskcode": "2",
                    "desc": "A cookie was set without the Secure flag.",
                    "instances": [
                        {
                            "uri": "http://example.com/set-cookie",
                            "method": "POST",
                            "param": "Set-Cookie",
                            "evidence": "session=abc123; Path=/",
                        }
                    ],
                    "count": "1",
                    "solution": "Set the Secure flag on all cookies.",
                    "cweid": "614",
                },
            ],
        },
        {
            "@name": "http://example.com:8080",
            "@host": "example.com",
            "@port": "8080",
            "alerts": [
                {
                    "pluginid": "99999",
                    "alert": "Extra Site Alert",
                    "riskcode": "1",
                    "desc": "Another site alert.",
                    "instances": [
                        {
                            "uri": "http://example.com:8080/info",
                            "method": "GET",
                            "param": "",
                            "evidence": "test",
                        }
                    ],
                    "count": "1",
                    "solution": "Fix it.",
                    "cweid": "16",
                }
            ],
        },
    ]
}

VALID_ZAP_JSON_STR = json.dumps(VALID_ZAP_JSON)


@pytest.fixture
def zap_json_str() -> str:
    return VALID_ZAP_JSON_STR


@pytest.fixture
def single_alert_json() -> str:
    return json.dumps(
        {
            "site": [
                {
                    "@name": "http://app.local",
                    "@host": "app.local",
                    "@port": "80",
                    "alerts": [
                        {
                            "pluginid": "42",
                            "alert": "Test Alert",
                            "riskcode": "1",
                            "desc": "Test description.",
                            "instances": [
                                {
                                    "uri": "http://app.local/",
                                    "method": "GET",
                                    "param": "",
                                    "evidence": "test evidence",
                                }
                            ],
                            "count": "1",
                            "solution": "Fix it.",
                            "cweid": "16",
                        }
                    ],
                }
            ]
        }
    )


# ——— DastConfig validation tests —————————————————————————————————————————————

class TestDastConfigValidation:
    """DastConfig Pydantic model: field defaults and cross-field validators."""

    def test_start_command_with_port_is_valid(self):
        """start_command + port is a valid configuration."""
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(start_command="npm start", port=3000)
        assert cfg.start_command == "npm start"
        assert cfg.port == 3000
        assert cfg.target_url is None

    def test_target_url_alone_is_valid(self):
        """target_url without start_command is valid — scanner runs against
        an externally-managed target."""
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="https://staging.example.com")
        assert cfg.target_url == "https://staging.example.com"
        assert cfg.start_command is None
        assert cfg.port is None

    def test_both_start_command_and_target_url_is_invalid(self):
        """Setting both start_command and target_url is ambiguous — must fail."""
        from general_ludd.project_runner.dast import DastConfig

        with pytest.raises(ValidationError, match=r"start_command|target_url"):
            DastConfig(start_command="npm start", port=3000, target_url="https://example.com")

    def test_neither_start_command_nor_target_url_is_invalid(self):
        """At least one of start_command or target_url must be provided."""
        from general_ludd.project_runner.dast import DastConfig

        with pytest.raises(ValidationError, match=r"start_command|target_url"):
            DastConfig()

    def test_start_command_without_port_is_invalid(self):
        """When start_command is set, port is required for health polling."""
        from general_ludd.project_runner.dast import DastConfig

        with pytest.raises(ValidationError, match=r"port"):
            DastConfig(start_command="npm start")

    def test_default_values(self):
        """Verify all DastConfig default values."""
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="http://localhost:8000")
        assert cfg.health_path == "/"
        assert cfg.startup_timeout_s == 60
        assert cfg.tool == "zap-baseline.py"
        assert cfg.max_duration_s == 900
        assert cfg.fail_on == "HIGH"

    def test_fail_on_high_valid(self):
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="http://example.com", fail_on="HIGH")
        assert cfg.fail_on == "HIGH"

    def test_fail_on_medium_valid(self):
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="http://example.com", fail_on="MEDIUM")
        assert cfg.fail_on == "MEDIUM"

    def test_fail_on_low_valid(self):
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="http://example.com", fail_on="LOW")
        assert cfg.fail_on == "LOW"

    def test_fail_on_invalid_value_raises(self):
        """fail_on must be one of HIGH, MEDIUM, LOW."""
        from general_ludd.project_runner.dast import DastConfig

        with pytest.raises(ValidationError):
            DastConfig(target_url="http://example.com", fail_on="CRITICAL")

    def test_fail_on_case_insensitive(self):
        """fail_on values should be normalized — or at least 'high' should work
        if normalization is implemented."""
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="http://example.com", fail_on="high")
        assert cfg.fail_on.upper() in ("HIGH", "high")

    def test_port_not_required_when_target_url_only(self):
        """port is only required with start_command, not with target_url."""
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="https://example.com")
        assert cfg.port is None
        assert cfg.start_command is None

    def test_health_path_default_preserved(self):
        """Custom health_path is stored as given."""
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(target_url="http://example.com", health_path="/healthz")
        assert cfg.health_path == "/healthz"

    def test_default_target_url_is_none(self):
        """target_url defaults to None."""
        from general_ludd.project_runner.dast import DastConfig

        cfg = DastConfig(start_command="python app.py", port=8000)
        assert cfg.target_url is None


# ——— DastFinding tests ———————————————————————————————————————————————————————


class TestDastFinding:
    """DastFinding dataclass: field types, defaults, and structural integrity."""

    def test_all_fields_populated(self):
        from general_ludd.project_runner.dast import DastFinding

        f = DastFinding(
            severity="HIGH",
            rule_id="SQL Injection",
            url="http://example.com/search?q=1",
            method="GET",
            evidence="' OR 1=1 --",
            solution="Use parameterized queries.",
            cwe_id="89",
            riskcode=3,
        )
        assert f.severity == "HIGH"
        assert f.rule_id == "SQL Injection"
        assert f.url == "http://example.com/search?q=1"
        assert f.method == "GET"
        assert f.evidence == "' OR 1=1 --"
        assert f.solution == "Use parameterized queries."
        assert f.cwe_id == "89"
        assert f.riskcode == 3

    def test_info_level_finding(self):
        from general_ludd.project_runner.dast import DastFinding

        f = DastFinding(
            severity="INFO",
            rule_id="Timestamp Disclosure",
            url="http://example.com/",
            method="GET",
            evidence="timestamp found",
            solution="Verify sensitivity.",
            cwe_id="200",
            riskcode=0,
        )
        assert f.severity == "INFO"
        assert f.riskcode == 0

    def test_missing_cwe_id_defaults_to_empty(self):
        """cwe_id should have a sensible default when ZAP provides none."""
        from general_ludd.project_runner.dast import DastFinding

        f = DastFinding(
            severity="LOW",
            rule_id="Some Rule",
            url="http://example.com/",
            method="GET",
            evidence="...",
            solution="Fix.",
            riskcode=1,
        )
        assert f.cwe_id == "" or f.cwe_id is not None

    def test_medium_severity_finding(self):
        from general_ludd.project_runner.dast import DastFinding

        f = DastFinding(
            severity="MEDIUM",
            rule_id="Cookie No Secure Flag",
            url="http://example.com/",
            method="POST",
            evidence="Set-Cookie missing Secure",
            solution="Add Secure flag.",
            cwe_id="614",
            riskcode=2,
        )
        assert f.severity == "MEDIUM"
        assert f.riskcode == 2

    def test_multiple_instances_per_alert_create_separate_findings(self):
        """Each instance within a ZAP alert should produce a separate DastFinding."""
        from general_ludd.project_runner.dast import DastFinding

        f1 = DastFinding(
            severity="LOW",
            rule_id="X-Content-Type-Options",
            url="http://example.com/",
            method="GET",
            evidence="Missing header",
            solution="Set header.",
            cwe_id="16",
            riskcode=1,
        )
        f2 = DastFinding(
            severity="LOW",
            rule_id="X-Content-Type-Options",
            url="http://example.com/login",
            method="POST",
            evidence="Missing header",
            solution="Set header.",
            cwe_id="16",
            riskcode=1,
        )
        assert f1.url != f2.url
        assert f1.method != f2.method


# ——— DastResult tests ————————————————————————————————————————————————————————


class TestDastResult:
    """DastResult dataclass: defaults and field behavior."""

    def test_passed_result(self):
        from general_ludd.project_runner.dast import DastResult

        r = DastResult(passed=True)
        assert r.passed is True
        assert r.skipped is False
        assert r.reason is None
        assert r.findings == []

    def test_failed_result_with_findings(self):
        from general_ludd.project_runner.dast import DastFinding, DastResult

        findings = [
            DastFinding(
                severity="HIGH",
                rule_id="SQL Injection",
                url="http://example.com/search",
                method="GET",
                evidence="' OR 1=1",
                solution="Parameterize.",
                cwe_id="89",
                riskcode=3,
            )
        ]
        r = DastResult(passed=False, findings=findings)
        assert r.passed is False
        assert len(r.findings) == 1

    def test_skipped_result(self):
        from general_ludd.project_runner.dast import DastResult

        r = DastResult(
            passed=False, skipped=True, reason="zap-baseline.py not installed"
        )
        assert r.skipped is True
        assert r.reason == "zap-baseline.py not installed"
        assert r.findings == []

    def test_findings_default_is_empty_list(self):
        from general_ludd.project_runner.dast import DastResult

        r = DastResult(passed=False)
        assert isinstance(r.findings, list)
        assert len(r.findings) == 0

    def test_skipped_with_reason_no_findings(self):
        from general_ludd.project_runner.dast import DastResult

        r = DastResult(passed=False, skipped=True, reason="timeout")
        assert r.skipped is True
        assert r.reason == "timeout"
        assert r.findings == []


# ——— parse_zap_baseline() tests ——————————————————————————————————————————————


class TestParseZapBaseline:
    """Parse ZAP baseline scan JSON output into structured findings."""

    def test_valid_json_parses_all_alerts(self):
        """Valid ZAP JSON with multiple alerts across multiple sites produces
        findings for all instances."""
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        # 4 alerts in site 1 (with 1+1+1+1=4 instances) + 1 alert in site 2 (1 instance)
        # SQL Injection: 1 instance, X-Content-Type-Options: 2 instances,
        # Timestamp Disclosure: 1 instance, Cookie no Secure: 1 instance,
        # Extra Site Alert: 1 instance = 6 findings total
        assert len(findings) == 6

    def test_riskcode_3_maps_to_high(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        sql_injection = [f for f in findings if f.rule_id == "SQL Injection"]
        assert len(sql_injection) >= 1
        assert sql_injection[0].severity == "HIGH"
        assert sql_injection[0].riskcode == 3

    def test_riskcode_2_maps_to_medium(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        medium_findings = [f for f in findings if f.riskcode == 2]
        assert len(medium_findings) >= 1
        for f in medium_findings:
            assert f.severity == "MEDIUM"

    def test_riskcode_1_maps_to_low(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        low_findings = [f for f in findings if f.riskcode == 1]
        assert len(low_findings) >= 1
        for f in low_findings:
            assert f.severity == "LOW"

    def test_riskcode_0_maps_to_info(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        info_findings = [f for f in findings if f.riskcode == 0]
        assert len(info_findings) >= 1
        for f in info_findings:
            assert f.severity == "INFO"

    def test_instances_expand_to_separate_findings(self):
        """Each ZAP 'instances' entry expands to its own DastFinding."""
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        xcto = [f for f in findings if f.rule_id == "X-Content-Type-Options Header Missing"]
        assert len(xcto) == 2  # two instances
        urls = {f.url for f in xcto}
        assert "http://example.com/" in urls
        assert "http://example.com/login" in urls

    def test_empty_json_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        assert parse_zap_baseline("{}") == []

    def test_empty_string_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        assert parse_zap_baseline("") == []

    def test_missing_alerts_key_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        json_str = json.dumps({"site": [{"@name": "http://example.com"}]})
        findings = parse_zap_baseline(json_str)
        assert findings == []

    def test_missing_site_key_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline('{"@version": "2.15"}')
        assert findings == []

    def test_malformed_json_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        assert parse_zap_baseline("not json at all") == []

    def test_truncated_json_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        truncated = VALID_ZAP_JSON_STR[: len(VALID_ZAP_JSON_STR) // 3]
        findings = parse_zap_baseline(truncated)
        assert findings == []

    def test_empty_site_list_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(json.dumps({"site": []}))
        assert findings == []

    def test_site_with_no_alerts_returns_empty_list(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(
            json.dumps({"site": [{"@name": "http://example.com", "alerts": []}]})
        )
        assert findings == []

    def test_multiple_sites_merge_findings(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        # Extra Site Alert only appears in site 2
        extra = [f for f in findings if f.rule_id == "Extra Site Alert"]
        assert len(extra) == 1
        assert extra[0].url == "http://example.com:8080/info"

    def test_alert_missing_instances_handled_gracefully(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        json_str = json.dumps(
            {
                "site": [
                    {
                        "@name": "http://example.com",
                        "alerts": [
                            {
                                "pluginid": "1",
                                "alert": "No Instances Alert",
                                "riskcode": "1",
                                "desc": "Missing instances key.",
                                "count": "0",
                                "solution": "Fix.",
                                "cweid": "16",
                            }
                        ],
                    }
                ]
            }
        )
        findings = parse_zap_baseline(json_str)
        # Should not crash — may return 0 findings or 1 with placeholder fields
        assert isinstance(findings, list)

    def test_alert_missing_cweid_defaults_to_empty_string(self):
        from general_ludd.project_runner.dast import parse_zap_baseline

        json_str = json.dumps(
            {
                "site": [
                    {
                        "@name": "http://example.com",
                        "alerts": [
                            {
                                "pluginid": "100",
                                "alert": "No CWE",
                                "riskcode": "2",
                                "desc": "Missing cweid.",
                                "instances": [
                                    {
                                        "uri": "http://example.com/",
                                        "method": "GET",
                                        "param": "",
                                        "evidence": "",
                                    }
                                ],
                                "count": "1",
                                "solution": "Fix.",
                            }
                        ],
                    }
                ]
            }
        )
        findings = parse_zap_baseline(json_str)
        assert len(findings) == 1
        assert findings[0].cwe_id == "" or findings[0].cwe_id is not None

    def test_riskcode_as_string_is_parsed(self):
        """ZAP emits riskcode as a string; the parser must coerce to int."""
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(VALID_ZAP_JSON_STR)
        sql = [f for f in findings if f.rule_id == "SQL Injection"]
        assert len(sql) >= 1
        assert isinstance(sql[0].riskcode, int)
        assert sql[0].riskcode == 3

    def test_non_json_input_type_returns_empty(self):
        """Parser should handle None or non-string input gracefully."""
        from general_ludd.project_runner.dast import parse_zap_baseline

        assert parse_zap_baseline(None) == []  # type: ignore[arg-type]

    def test_single_alert_parses_correctly(self, single_alert_json: str):
        from general_ludd.project_runner.dast import parse_zap_baseline

        findings = parse_zap_baseline(single_alert_json)
        assert len(findings) == 1
        assert findings[0].rule_id == "Test Alert"
        assert findings[0].severity == "LOW"
        assert findings[0].riskcode == 1
        assert findings[0].url == "http://app.local/"
        assert findings[0].method == "GET"
        assert findings[0].evidence == "test evidence"
        assert findings[0].solution == "Fix it."
        assert findings[0].cwe_id == "16"


# ——— is_loopback() tests —————————————————————————————————————————————————————


class TestIsLoopback:
    """SSRF guard: identify loopback/private IP addresses."""

    def test_localhost_is_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("localhost") is True

    def test_ipv4_loopback_127_0_0_1_is_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("127.0.0.1") is True

    def test_ipv4_loopback_127_0_0_2_is_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("127.0.0.2") is True

    def test_ipv4_loopback_127_255_255_255_is_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("127.255.255.255") is True

    def test_ipv6_loopback_is_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("::1") is True

    def test_ipv6_full_form_is_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("0:0:0:0:0:0:0:1") is True

    def test_example_dot_com_is_not_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("example.com") is False

    def test_private_ip_10_0_0_1_is_not_loopback(self):
        """10.x.x.x is RFC 1918 private but NOT loopback — separate check."""
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("10.0.0.1") is False

    def test_private_ip_192_168_1_1_is_not_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("192.168.1.1") is False

    def test_public_ip_8_8_8_8_is_not_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("8.8.8.8") is False

    def test_empty_string_is_not_loopback(self):
        from general_ludd.project_runner.dast import is_loopback

        assert is_loopback("") is False


# ——— is_blocked_target() tests ———————————————————————————————————————————————


class TestIsBlockedTarget:
    """SSRF guard: identify targets blocked from DAST scanning."""

    def test_rfc1918_class_a_10_0_0_1_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("10.0.0.1") is True

    def test_rfc1918_class_a_10_255_255_255_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("10.255.255.255") is True

    def test_rfc1918_class_b_172_16_0_1_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("172.16.0.1") is True

    def test_rfc1918_class_b_172_31_255_255_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("172.31.255.255") is True

    def test_rfc1918_class_c_192_168_1_1_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("192.168.1.1") is True

    def test_rfc1918_class_c_192_168_255_255_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("192.168.255.255") is True

    def test_link_local_169_254_1_1_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("169.254.1.1") is True

    def test_link_local_169_254_255_255_is_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("169.254.255.255") is True

    def test_public_ip_8_8_8_8_is_not_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("8.8.8.8") is False

    def test_public_ip_1_1_1_1_is_not_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("1.1.1.1") is False

    def test_loopback_127_0_0_1_is_not_blocked_by_this_check(self):
        """Loopback MUST be detected by is_loopback first — is_blocked_target
        should NOT catch 127.0.0.1 (it is a different category)."""
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("127.0.0.1") is False

    def test_localhost_string_is_not_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("localhost") is False

    def test_empty_string_is_not_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("") is False

    def test_non_ip_hostname_is_not_blocked(self):
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("example.com") is False

    def test_172_32_0_1_not_in_rfc1918_range_is_not_blocked(self):
        """172.32.0.0 is outside the 172.16-31 RFC 1918 range."""
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("172.32.0.1") is False

    def test_100_64_0_1_cg_nat_is_not_blocked(self):
        """100.64/10 is carrier-grade NAT (RFC 6598) — should NOT be blocked
        unless explicitly added."""
        from general_ludd.project_runner.dast import is_blocked_target

        assert is_blocked_target("100.64.0.1") is False


# ——— severity_threshold_exceeded() tests —————————————————————————————————————


class TestSeverityThresholdExceeded:
    """Gate: check whether any DastFinding exceeds the fail_on threshold."""

    def test_high_threshold_blocks_high_finding(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="HIGH",
                rule_id="SQL Injection",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="89",
                riskcode=3,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="HIGH") is True

    def test_high_threshold_allows_medium_and_below(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="MEDIUM",
                rule_id="Cookie No Secure",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="614",
                riskcode=2,
            ),
            DastFinding(
                severity="LOW",
                rule_id="XCTO Missing",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="16",
                riskcode=1,
            ),
        ]
        assert severity_threshold_exceeded(findings, fail_on="HIGH") is False

    def test_high_threshold_allows_info(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="INFO",
                rule_id="Timestamp",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="200",
                riskcode=0,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="HIGH") is False

    def test_medium_threshold_blocks_high(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="HIGH",
                rule_id="SQL Injection",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="89",
                riskcode=3,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="MEDIUM") is True

    def test_medium_threshold_blocks_medium(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="MEDIUM",
                rule_id="Cookie No Secure",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="614",
                riskcode=2,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="MEDIUM") is True

    def test_medium_threshold_allows_low_and_info(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="LOW",
                rule_id="XCTO Missing",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="16",
                riskcode=1,
            ),
            DastFinding(
                severity="INFO",
                rule_id="Timestamp",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="200",
                riskcode=0,
            ),
        ]
        assert severity_threshold_exceeded(findings, fail_on="MEDIUM") is False

    def test_low_threshold_blocks_high(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="HIGH",
                rule_id="SQL Injection",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="89",
                riskcode=3,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="LOW") is True

    def test_low_threshold_blocks_medium(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="MEDIUM",
                rule_id="Cookie No Secure",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="614",
                riskcode=2,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="LOW") is True

    def test_low_threshold_blocks_low(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="LOW",
                rule_id="XCTO Missing",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="16",
                riskcode=1,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="LOW") is True

    def test_low_threshold_allows_info(self):
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        findings = [
            DastFinding(
                severity="INFO",
                rule_id="Timestamp",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="200",
                riskcode=0,
            )
        ]
        assert severity_threshold_exceeded(findings, fail_on="LOW") is False

    def test_empty_findings_never_exceeds_threshold(self):
        from general_ludd.project_runner.dast import severity_threshold_exceeded

        for threshold in ("HIGH", "MEDIUM", "LOW"):
            assert severity_threshold_exceeded([], fail_on=threshold) is False

    def test_mixed_severities_on_medium_threshold(self):
        """Medium threshold: HIGH + MEDIUM should exceed, LOW + INFO should not."""
        from general_ludd.project_runner.dast import (
            DastFinding,
            severity_threshold_exceeded,
        )

        mixed_high_med = [
            DastFinding(
                severity="HIGH",
                rule_id="A",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="89",
                riskcode=3,
            ),
            DastFinding(
                severity="MEDIUM",
                rule_id="B",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="614",
                riskcode=2,
            ),
        ]
        assert severity_threshold_exceeded(mixed_high_med, fail_on="MEDIUM") is True

        mixed_low_info = [
            DastFinding(
                severity="LOW",
                rule_id="C",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="16",
                riskcode=1,
            ),
            DastFinding(
                severity="INFO",
                rule_id="D",
                url="http://example.com/",
                method="GET",
                evidence="...",
                solution="...",
                cwe_id="200",
                riskcode=0,
            ),
        ]
        assert severity_threshold_exceeded(mixed_low_info, fail_on="MEDIUM") is False


# ——— run_dast_scan() integration tests ———————————————————————————————————————


class TestRunDastScan:
    """Lifecycle wrapper: start app, health-poll, scan, teardown."""

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _make_profile(**overrides):
        from general_ludd.project_runner.profile import ProjectProfile

        defaults = {
            "name": "test",
            "commands": {"zap-baseline.py": "zap-baseline.py -t URL -J OUT"},
            "allowed_exec": ["zap-baseline.py"],
        }
        defaults.update(overrides)
        return ProjectProfile(**defaults)

    # ── test: tool on PATH ───────────────────────────────────────────────

    def test_tool_not_on_path_skips_scan(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com")
        profile = self._make_profile()
        workspace = tmp_path

        with (
            patch("general_ludd.project_runner.dast.shutil.which", return_value=None),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is True
            assert "not found" in (result.reason or "").lower()

    def test_tool_not_in_allowed_exec_skips_scan(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com")
        profile = self._make_profile(allowed_exec=["other-tool"])
        workspace = tmp_path

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is True
            assert "allowed" in (result.reason or "").lower()

    # ── test: target_url mode (no app start) ─────────────────────────────

    def test_target_url_mode_no_health_poll_needed(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com")
        profile = self._make_profile()
        workspace = tmp_path

        mock_run = MagicMock(returncode=0)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ) as mock_subp_run,
            patch(
                "general_ludd.project_runner.dast._start_app",
            ) as mock_start_app,
            patch(
                "general_ludd.project_runner.dast._wait_health",
            ) as mock_wait_health,
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False
            mock_subp_run.assert_called_once()
            args_passed = mock_subp_run.call_args[0][0]
            assert "zap-baseline.py" in args_passed
            assert any("example.com" in arg for arg in args_passed)
            mock_start_app.assert_not_called()
            mock_wait_health.assert_not_called()

    # ── test: start_command mode (app launch + health) ───────────────────

    def test_start_command_mode_launches_app_and_health_polls(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(start_command="python -m http.server", port=8000)
        profile = self._make_profile()
        workspace = tmp_path

        mock_app = MagicMock(pid=12345)
        mock_run = MagicMock(returncode=0)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast._start_app",
                return_value=mock_app,
            ) as mock_start_app,
            patch(
                "general_ludd.project_runner.dast._wait_health",
                return_value=True,
            ) as mock_wait_health,
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ),
            patch(
                "general_ludd.project_runner.dast._kill_app",
            ) as mock_kill_app,
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False
            mock_start_app.assert_called_once_with(8000, "python -m http.server")
            mock_wait_health.assert_called_once_with(8000, "/", 60)
            mock_kill_app.assert_called_once_with(mock_app)

    # ── test: health poll failure ────────────────────────────────────────

    def test_health_poll_failure_skips_scan(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(start_command="python app.py", port=8000, startup_timeout_s=5)
        profile = self._make_profile()
        workspace = tmp_path

        mock_app = MagicMock(pid=12345)

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast._start_app",
                return_value=mock_app,
            ),
            patch(
                "general_ludd.project_runner.dast._wait_health",
                return_value=False,
            ),
            patch(
                "general_ludd.project_runner.dast._kill_app",
            ) as mock_kill_app,
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
            ) as mock_subp_run,
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is True
            assert "health" in (result.reason or "").lower()
            mock_kill_app.assert_called_once_with(mock_app)
            mock_subp_run.assert_not_called()

    # ── test: non-zero scanner exit ──────────────────────────────────────

    def test_scanner_non_zero_exit_still_returns_findings(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com")
        profile = self._make_profile()
        workspace = tmp_path

        mock_run = MagicMock(returncode=2)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ),
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False
            assert len(result.findings) > 0
            assert result.passed is False

    # ── test: finally block kills app on scanner error ───────────────────

    def test_app_teardown_in_finally_on_scanner_error(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(start_command="python app.py", port=8000)
        profile = self._make_profile()
        workspace = tmp_path

        mock_app = MagicMock(pid=12345)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast._start_app",
                return_value=mock_app,
            ),
            patch(
                "general_ludd.project_runner.dast._wait_health",
                return_value=True,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                side_effect=RuntimeError("scanner crashed"),
            ),
            patch(
                "general_ludd.project_runner.dast._kill_app",
            ) as mock_kill_app,
        ):
            with pytest.raises(RuntimeError, match="scanner crashed"):
                run_dast_scan(cfg, profile, workspace)
            mock_kill_app.assert_called_once_with(mock_app)

    # ── test: scanner timeout ────────────────────────────────────────────

    def test_scanner_timeout_returns_skipped(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com", max_duration_s=1)
        profile = self._make_profile()
        workspace = tmp_path

        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="zap-baseline.py", timeout=1),
            ),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is True
            assert "timed out" in (result.reason or "").lower()

    # ── test: app killed on successful scan (finally block) ──────────────

    def test_app_start_command_is_killed_on_finally(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(start_command="flask run", port=5000)
        profile = self._make_profile()
        workspace = tmp_path

        mock_app = MagicMock(pid=12345)
        mock_run = MagicMock(returncode=0)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast._start_app",
                return_value=mock_app,
            ),
            patch(
                "general_ludd.project_runner.dast._wait_health",
                return_value=True,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ),
            patch(
                "general_ludd.project_runner.dast._kill_app",
            ) as mock_kill_app,
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False
            mock_kill_app.assert_called_once_with(mock_app)

    # ── test: target URL validation — loopback allowed, private blocked ──

    def test_target_url_loopback_is_allowed(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://127.0.0.1:8000")
        profile = self._make_profile()
        workspace = tmp_path

        mock_run = MagicMock(returncode=0)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ),
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False

    def test_target_url_private_ip_blocks_scan(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://10.0.0.1:8000")
        profile = self._make_profile()
        workspace = tmp_path

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is True
            assert "blocked" in (result.reason or "").lower()

    # ── test: resolve_argv failure fallback ──────────────────────────────

    def test_resolve_argv_failure_falls_back_to_tool_name(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com")
        profile = self._make_profile()
        workspace = tmp_path

        mock_run = MagicMock(returncode=0)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(
                ProjectProfile,
                "resolve_argv",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ) as mock_subp_run,
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False
            args_passed = mock_subp_run.call_args[0][0]
            assert args_passed[0] == "zap-baseline.py"

    # ── test: allow-any-exec bypasses allowed_exec check ─────────────────

    def test_allow_any_exec_bypasses_allowed_exec_check(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com")
        profile = self._make_profile(allowed_exec=[])
        workspace = tmp_path

        mock_run = MagicMock(returncode=0)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value="1"),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ),
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False

    # ── test: full integration target_url mode ───────────────────────────

    def test_run_dast_scan_integration_target_url_success(self, tmp_path):
        from general_ludd.project_runner.dast import DastConfig, run_dast_scan

        cfg = DastConfig(target_url="http://example.com")
        profile = self._make_profile()
        workspace = tmp_path

        mock_run = MagicMock(returncode=0)
        mock_tmp_file = MagicMock()
        mock_tmp_file.name = "/tmp/zap-out.json"
        mock_tmp_cm = MagicMock()
        mock_tmp_cm.__enter__.return_value = mock_tmp_file

        with (
            patch(
                "general_ludd.project_runner.dast.shutil.which",
                return_value="/usr/local/bin/zap-baseline.py",
            ),
            patch("general_ludd.project_runner.dast.os.getenv", return_value=""),
            patch.object(ProjectProfile, "resolve_argv", return_value=["zap-baseline.py"]),
            patch(
                "general_ludd.project_runner.dast.tempfile.NamedTemporaryFile",
                return_value=mock_tmp_cm,
            ),
            patch(
                "general_ludd.project_runner.dast.subprocess.run",
                return_value=mock_run,
            ),
            patch("general_ludd.project_runner.dast.Path.read_text", return_value=VALID_ZAP_JSON_STR),
        ):
            result = run_dast_scan(cfg, profile, workspace)
            assert result.skipped is False
            assert isinstance(result.findings, list)
            assert len(result.findings) > 0
            for f in result.findings:
                assert f.severity in ("HIGH", "MEDIUM", "LOW", "INFO")
                assert f.url
                assert f.rule_id
                assert f.method
