"""CVE dependency upgrade checker — audits known CVEs in the dependency tree.

Provides :func:`check_known_cves` which scans the installed package set
against a curated advisory database (:data:`KNOWN_CVES`) to flag packages
that should be upgraded.  Designed as a pre-release gate: call it from
``make dist`` or ``make check-cves`` so a release is never cut with a
known-vulnerable dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast


@dataclass
class CveFinding:
    package: str
    installed: str
    fixed_in: str
    cve_id: str
    severity: str  # "critical" / "high" / "medium" / "low"
    description: str = ""


KNOWN_CVES: dict[str, dict[str, object]] = {
    "diskcache": {
        "cve": "CVE-2025-69872",
        "fixed_in": "5.6.2",
        "severity": "medium",
        "description": "diskcache SQL injection via cache key",
    },
    "pip": {
        "cve": "PYSEC-2026-196",
        "fixed_in": "25.0",
        "severity": "low",
        "description": "pip mercurial injection via crafted VCS URL",
    },
}

DEFAULT_SEVERITY_THRESHOLD = "low"


SEVERITY_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _installed_version(package_name: str) -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version(package_name)
    except (PackageNotFoundError, ModuleNotFoundError):
        return None


def check_known_cves(
    *,
    severity_threshold: str = DEFAULT_SEVERITY_THRESHOLD,
) -> list[CveFinding]:
    threshold_rank = SEVERITY_RANK.get(severity_threshold.lower(), 0)
    findings: list[CveFinding] = []

    for pkg, advisory in KNOWN_CVES.items():
        inst = _installed_version(pkg)
        if inst is None:
            continue
        sev = cast(str, advisory.get("severity", "low"))
        if SEVERITY_RANK.get(sev.lower(), 0) < threshold_rank:
            continue
        findings.append(
            CveFinding(
                package=pkg,
                installed=inst,
                fixed_in=cast(str, advisory.get("fixed_in", "")),
                cve_id=cast(str, advisory.get("cve", "")),
                severity=sev,
                description=cast(str, advisory.get("description", "")),
            )
        )

    return findings


def cve_check_passes(*, severity_threshold: str = DEFAULT_SEVERITY_THRESHOLD) -> bool:
    return len(check_known_cves(severity_threshold=severity_threshold)) == 0
