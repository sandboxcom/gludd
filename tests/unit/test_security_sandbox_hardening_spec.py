"""Structural contract for the security and sandbox hardening specification."""

from __future__ import annotations

import re
from pathlib import Path

SPEC_PATH = Path("docs/specs/FEATURE_SECURITY_SANDBOX_HARDENING.md")

OPEN_BACKLOG_IDS = {
    "D-08",
    "D-09",
    "D-11",
    "D-12",
    "D-13",
    "D-15",
    "D-16",
    "D-17",
    "D-19",
    "D-20",
    "D-21",
    "D-22",
    "D-23",
    "D-24",
    "D-26",
    "D-30",
}

REQUIRED_CONTRACT_IDS = {
    "SH-CONFIG-001",
    "SH-SBX-001",
    "SH-FS-001",
    "SH-NET-001",
    "SH-PROC-001",
    "SH-SECRET-001",
    "SH-RESOURCE-001",
    "SH-AUDIT-001",
    "SH-ZDD-001",
    "SH-BANDIT-001",
    "SH-DEP-001",
    "SH-ACCEPT-001",
}


def _read_spec() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def test_spec_exists_and_is_explicitly_not_implemented() -> None:
    text = _read_spec()
    assert "**Status:** Proposed" in text
    assert "does not claim" in text


def test_spec_maps_every_currently_open_backlog_control() -> None:
    text = _read_spec()
    mapped_ids = set(re.findall(r"^\| (D-\d{2}) \|", text, flags=re.MULTILINE))
    assert mapped_ids == OPEN_BACKLOG_IDS


def test_spec_defines_all_boundary_and_delivery_contracts() -> None:
    text = _read_spec()
    for contract_id in REQUIRED_CONTRACT_IDS:
        assert f"### {contract_id}" in text, f"missing {contract_id}"


def test_spec_covers_current_bandit_and_dependency_baselines() -> None:
    text = _read_spec()
    assert "SEVERITY.HIGH: 0" in text
    assert "SEVERITY.MEDIUM: 46" in text
    assert "SEVERITY.LOW: 506" in text
    for finding_id in ("B324", "B314", "B318", "B608", "B603", "B607"):
        assert finding_id in text
    for dependency in (
        "Pillow 12.3.0",
        "safehttpx 0.1.7",
        "No known vulnerabilities found",
    ):
        assert dependency in text


def test_spec_records_primary_and_long_lived_user_issue_research() -> None:
    text = _read_spec()
    for required_url in (
        "firecracker/blob/main/docs/prod-host-setup.md",
        "gvisor.dev/docs/architecture_guide/security/",
        "kernel.org/doc/html/latest/userspace-api/landlock.html",
        "kernel.org/doc/html/v5.9/userspace-api/seccomp_filter.html",
        "containers/bubblewrap/issues/324",
        "google/nsjail/issues/236",
        "developer.apple.com/forums/thread/661939",
        "pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html",
    ):
        assert required_url in text


def test_spec_has_executable_acceptance_and_coverage_gates() -> None:
    text = _read_spec()
    assert "security-backlog-strict" in text
    assert "sast-gate" in text
    assert "sandbox-contract" in text
    assert "85%" in text
    assert "75%" in text
