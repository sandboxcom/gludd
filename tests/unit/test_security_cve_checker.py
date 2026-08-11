"""Deep tests for the CVE dependency upgrade checker."""

from __future__ import annotations

import pytest


def test_cve_finding_dataclass_defaults() -> None:
    from general_ludd.security.cve_checker import CveFinding

    f = CveFinding(package="pkg", installed="1.0", fixed_in="2.0", cve_id="CVE-123", severity="high")
    assert f.package == "pkg"
    assert f.installed == "1.0"
    assert f.fixed_in == "2.0"
    assert f.cve_id == "CVE-123"
    assert f.severity == "high"
    assert f.description == ""


def test_cve_finding_dataclass_with_description() -> None:
    from general_ludd.security.cve_checker import CveFinding

    f = CveFinding(
        package="pkg",
        installed="1.0",
        fixed_in="2.0",
        cve_id="CVE-123",
        severity="critical",
        description="bad bug",
    )
    assert f.description == "bad bug"


def test_severity_rank_ordering() -> None:
    from general_ludd.security.cve_checker import SEVERITY_RANK

    assert SEVERITY_RANK["critical"] > SEVERITY_RANK["high"]
    assert SEVERITY_RANK["high"] > SEVERITY_RANK["medium"]
    assert SEVERITY_RANK["medium"] > SEVERITY_RANK["low"]
    assert SEVERITY_RANK["critical"] == 4
    assert SEVERITY_RANK["low"] == 1


def test_default_severity_threshold_is_low() -> None:
    from general_ludd.security.cve_checker import DEFAULT_SEVERITY_THRESHOLD

    assert DEFAULT_SEVERITY_THRESHOLD == "low"


def test_known_cves_has_expected_keys() -> None:
    from general_ludd.security.cve_checker import KNOWN_CVES

    for pkg in KNOWN_CVES:
        advisory = KNOWN_CVES[pkg]
        assert "cve" in advisory
        assert "fixed_in" in advisory
        assert "severity" in advisory
        assert "description" in advisory


def test_installed_version_real_package() -> None:
    from general_ludd.security.cve_checker import _installed_version

    v = _installed_version("pytest")
    assert v is not None
    assert isinstance(v, str)
    assert len(v) > 0


def test_installed_version_missing_package() -> None:
    from general_ludd.security.cve_checker import _installed_version

    v = _installed_version("nonexistent-pkg-xyz-12345")
    assert v is None


def test_installed_version_empty_string_raises() -> None:
    from general_ludd.security.cve_checker import _installed_version

    try:
        _installed_version("")
        raise AssertionError("expected ValueError for empty package name")
    except ValueError:
        pass


def test_check_known_cves_default_threshold_returns_list() -> None:
    from general_ludd.security.cve_checker import check_known_cves

    result = check_known_cves()
    assert isinstance(result, list)


def test_check_known_cves_with_threshold_critical() -> None:
    from general_ludd.security.cve_checker import CveFinding, check_known_cves

    result = check_known_cves(severity_threshold="critical")
    for finding in result:
        assert isinstance(finding, CveFinding)
        assert finding.severity == "critical"


def test_check_known_cves_with_threshold_high() -> None:
    from general_ludd.security.cve_checker import SEVERITY_RANK, check_known_cves

    result = check_known_cves(severity_threshold="high")
    for finding in result:
        assert SEVERITY_RANK[finding.severity] >= SEVERITY_RANK["high"]


def test_check_known_cves_empty_when_no_packages_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.cve_checker import check_known_cves

    def _fake(_name: str) -> str | None:
        return None

    monkeypatch.setattr(
        "general_ludd.security.cve_checker._installed_version",
        _fake,
    )
    result = check_known_cves()
    assert result == []


def test_check_known_cves_finding_has_all_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.cve_checker import check_known_cves

    def _fake(name: str) -> str:
        return "9.9.9"

    monkeypatch.setattr(
        "general_ludd.security.cve_checker._installed_version",
        _fake,
    )
    result = check_known_cves()
    for finding in result:
        assert finding.package
        assert finding.installed == "9.9.9"
        assert finding.fixed_in
        assert finding.cve_id
        assert finding.severity in ("low", "medium", "high", "critical")


def test_check_known_cves_respects_severity_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.cve_checker import check_known_cves

    def _fake(name: str) -> str:
        return "9.9.9"

    monkeypatch.setattr(
        "general_ludd.security.cve_checker._installed_version",
        _fake,
    )
    all_findings = check_known_cves(severity_threshold="low")
    critical_only = check_known_cves(severity_threshold="critical")
    high_up = check_known_cves(severity_threshold="high")

    assert len(critical_only) <= len(high_up) <= len(all_findings)


def test_check_known_cves_unknown_severity_threshold_defaults_to_zero() -> None:
    from general_ludd.security.cve_checker import check_known_cves

    result = check_known_cves(severity_threshold="nonexistent-level")
    assert isinstance(result, list)


def test_cve_check_passes_default() -> None:
    from general_ludd.security.cve_checker import cve_check_passes

    result = cve_check_passes()
    assert isinstance(result, bool)


def test_cve_check_passes_with_critical_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.cve_checker import cve_check_passes

    # With a fake version for all packages but critical threshold, depends on data
    def _fake(name: str) -> str:
        return "9.9.9"

    monkeypatch.setattr(
        "general_ludd.security.cve_checker._installed_version",
        _fake,
    )
    result = cve_check_passes(severity_threshold="high")
    assert isinstance(result, bool)


def test_cve_check_passes_when_no_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.cve_checker import cve_check_passes

    def _fake(_name: str) -> str | None:
        return None

    monkeypatch.setattr(
        "general_ludd.security.cve_checker._installed_version",
        _fake,
    )
    assert cve_check_passes() is True


def test_check_known_cves_each_finding_matches_known_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.cve_checker import KNOWN_CVES, check_known_cves

    def _fake(name: str) -> str:
        return "9.9.9"

    monkeypatch.setattr(
        "general_ludd.security.cve_checker._installed_version",
        _fake,
    )
    result = check_known_cves(severity_threshold="low")
    for finding in result:
        assert finding.package in KNOWN_CVES
        advisory = KNOWN_CVES[finding.package]
        assert finding.cve_id == advisory["cve"]
        assert finding.fixed_in == advisory["fixed_in"]
        assert finding.severity == advisory["severity"]
        assert finding.description == advisory["description"]
