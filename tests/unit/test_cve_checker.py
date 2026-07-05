"""Tests for CVE dependency upgrade checker."""

from __future__ import annotations

from unittest.mock import patch

from general_ludd.security.cve_checker import (
    KNOWN_CVES,
    SEVERITY_RANK,
    CveFinding,
    check_known_cves,
    cve_check_passes,
)


class TestCveFindingDataclass:
    def test_fields(self) -> None:
        f = CveFinding(
            package="diskcache",
            installed="5.6.1",
            fixed_in="5.6.2",
            cve_id="CVE-2025-69872",
            severity="medium",
            description="test",
        )
        assert f.package == "diskcache"
        assert f.installed == "5.6.1"
        assert f.fixed_in == "5.6.2"
        assert f.cve_id == "CVE-2025-69872"
        assert f.severity == "medium"
        assert f.description == "test"


class TestCheckKnownCves:
    def test_returns_empty_when_no_vulnerable_packages_installed(self) -> None:
        with patch(
            "general_ludd.security.cve_checker._installed_version",
            return_value=None,
        ):
            findings = check_known_cves()
            assert findings == []

    def test_returns_findings_when_vulnerable_package_installed(self) -> None:
        with patch(
            "general_ludd.security.cve_checker._installed_version",
            side_effect=lambda pkg: "5.6.1" if pkg == "diskcache" else None,
        ):
            findings = check_known_cves()
            assert len(findings) >= 1
            diskcache_finding = [f for f in findings if f.package == "diskcache"]
            assert len(diskcache_finding) == 1
            assert diskcache_finding[0].installed == "5.6.1"
            assert diskcache_finding[0].cve_id == "CVE-2025-69872"

    def test_respects_severity_threshold(self) -> None:
        with patch(
            "general_ludd.security.cve_checker._installed_version",
            side_effect=lambda pkg: "5.6.1" if pkg == "diskcache" else "24.0" if pkg == "pip" else None,
        ):
            findings = check_known_cves(severity_threshold="high")
            assert len(findings) == 0

    def test_severity_threshold_medium_includes_medium_and_above(self) -> None:
        with patch(
            "general_ludd.security.cve_checker._installed_version",
            side_effect=lambda pkg: "5.6.1" if pkg == "diskcache" else "24.0" if pkg == "pip" else None,
        ):
            findings = check_known_cves(severity_threshold="medium")
            assert len(findings) >= 1
            severities = {f.severity for f in findings}
            assert "low" not in severities or all(
                f.severity == "low"
                for f in findings
                if SEVERITY_RANK.get(f.severity, 0) >= SEVERITY_RANK["medium"]
            )

    def test_unknown_severity_threshold_treated_as_zero(self) -> None:
        with patch(
            "general_ludd.security.cve_checker._installed_version",
            return_value=None,
        ):
            findings = check_known_cves(severity_threshold="nonsense")
            assert findings == []

    def test_known_cves_has_diskcache_entry(self) -> None:
        assert "diskcache" in KNOWN_CVES
        assert KNOWN_CVES["diskcache"]["cve"] == "CVE-2025-69872"

    def test_known_cves_has_pip_entry(self) -> None:
        assert "pip" in KNOWN_CVES
        assert KNOWN_CVES["pip"]["cve"] == "PYSEC-2026-196"


class TestCveCheckPasses:
    def test_passes_when_no_findings(self) -> None:
        with patch(
            "general_ludd.security.cve_checker._installed_version",
            return_value=None,
        ):
            assert cve_check_passes() is True

    def test_fails_when_findings_exist(self) -> None:
        with patch(
            "general_ludd.security.cve_checker._installed_version",
            side_effect=lambda pkg: "5.6.1" if pkg == "diskcache" else None,
        ):
            assert cve_check_passes() is False


class TestSeverityRank:
    def test_critical_highest(self) -> None:
        assert SEVERITY_RANK["critical"] > SEVERITY_RANK["high"]
        assert SEVERITY_RANK["critical"] > SEVERITY_RANK["medium"]
        assert SEVERITY_RANK["critical"] > SEVERITY_RANK["low"]

    def test_high_above_medium(self) -> None:
        assert SEVERITY_RANK["high"] > SEVERITY_RANK["medium"]
        assert SEVERITY_RANK["high"] > SEVERITY_RANK["low"]

    def test_low_smallest(self) -> None:
        assert SEVERITY_RANK["low"] == 1
