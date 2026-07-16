"""Tests for the OS compliance report generator (module_utils/compliance_report.py).

TDD: written BEFORE the implementation. Covers STIG compliance status,
NIST SP 800-53 control mapping, per-benchmark CIS scoring, the phased
remediation roadmap, markdown rendering, serialization, and edge cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
if str(COLLECTION_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTION_ROOT))

from module_utils.compliance_report import (  # noqa: E402
    CISControlStatus,
    CISScore,
    ComplianceReport,
    NistControlMapping,
    RemediationPhase,
    StigStatus,
    format_compliance_markdown,
    generate_compliance_report,
)
from module_utils.hardening_guide import HARDENING_KB  # noqa: E402


# ---- fixtures ----------------------------------------------------------------

@pytest.fixture
def linux_findings() -> list[dict]:
    return [
        {"id": "LSEC-SELINUX-001", "severity": "high", "category": "selinux",
         "description": "SELinux is disabled"},
        {"id": "LSEC-KERNEL-001", "severity": "high", "category": "kernel",
         "description": "ASLR not fully enabled"},
        {"id": "LSEC-AUDITD-001", "severity": "medium", "category": "auditd",
         "description": "auditd not installed"},
        {"id": "LSEC-KERNEL-003", "severity": "low", "category": "kernel",
         "description": "dmesg_restrict not set"},
    ]


@pytest.fixture
def windows_findings() -> list[dict]:
    return [
        {"id": "WSEC-DEF-001", "severity": "critical", "category": "defender",
         "description": "Defender AV disabled"},
        {"id": "WSEC-FW-001", "severity": "critical", "category": "firewall",
         "description": "Firewall disabled on Domain profile"},
        {"id": "WSEC-PW-002", "severity": "medium", "category": "password_policy",
         "description": "Min password length below 8"},
    ]


@pytest.fixture
def mixed_findings(linux_findings, windows_findings) -> list[dict]:
    return linux_findings + windows_findings


# ---- STIG compliance ---------------------------------------------------------

class TestStigCompliance:
    def test_findings_with_stig_refs_are_non_compliant(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        stig_map = {s.stig_id: s for s in report.stig}
        # LSEC-SELINUX-001 KB references "STIG RHEL-09-651015"
        assert "RHEL-09-651015" in stig_map
        assert stig_map["RHEL-09-651015"].status == "non_compliant"
        assert stig_map["RHEL-09-651015"].finding_id == "LSEC-SELINUX-001"

    def test_kb_stig_controls_not_triggered_are_not_assessed(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        stig_map = {s.stig_id: s for s in report.stig}
        # WSEC-FW-001 references "STIG WN10-00-000005" — not in linux_findings
        assert "WN10-00-000005" in stig_map
        assert stig_map["WN10-00-000005"].status == "not_assessed"
        assert stig_map["WN10-00-000005"].finding_id is None

    def test_every_stig_id_in_kb_appears_in_report(self):
        report = generate_compliance_report([])
        stig_ids = {s.stig_id for s in report.stig}
        for fid, tmpl in HARDENING_KB.items():
            for ref in tmpl.get("references", []):
                if ref.startswith("STIG "):
                    sid = ref.split("STIG ", 1)[1].strip()
                    assert sid in stig_ids, f"{fid}: STIG id {sid} missing from report"

    def test_stig_status_enum_is_constrained(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        for s in report.stig:
            assert s.status in ("non_compliant", "not_assessed")

    def test_stig_entry_carries_severity(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        for s in report.stig:
            assert s.severity in ("critical", "high", "medium", "low", "info")


# ---- NIST 800-53 mapping -----------------------------------------------------

class TestNistMapping:
    def test_findings_grouped_by_nist_control(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        ctrl_map = {m.control: m for m in report.nist_800_53}
        # LSEC-SELINUX-001 and LSEC-KERNEL-001 both map to AC-3 / SC-30
        assert "AC-3" in ctrl_map
        assert "LSEC-SELINUX-001" in ctrl_map["AC-3"].finding_ids

    def test_nist_control_has_title(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        for m in report.nist_800_53:
            assert isinstance(m.title, str) and m.title

    def test_nist_control_severity_is_max_of_findings(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        ctrl_map = {m.control: m for m in report.nist_800_53}
        # AC-3 has LSEC-SELINUX-001 (high) and LSEC-AUDITD-001 (medium) is AU-2
        # LSEC-SELINUX-001 is high -> AC-3 severity should be high
        assert ctrl_map["AC-3"].severity == "high"

    def test_nist_control_finding_ids_non_empty(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        for m in report.nist_800_53:
            assert m.finding_ids, f"{m.control}: empty finding_ids"

    def test_no_duplicate_finding_ids_per_control(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        for m in report.nist_800_53:
            assert len(m.finding_ids) == len(set(m.finding_ids)), (
                f"{m.control}: duplicate finding_ids"
            )


# ---- CIS score ---------------------------------------------------------------

class TestCISScore:
    def test_one_score_per_benchmark(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        benchmarks = {s.benchmark for s in report.cis_scores}
        assert "CIS-RHEL9" in benchmarks
        assert "CIS-Win11" in benchmarks

    def test_score_is_percentage_0_to_100(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        for s in report.cis_scores:
            assert 0.0 <= s.score_percent <= 100.0
            assert isinstance(s.score_percent, float)

    def test_failed_controls_subset_of_total(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        for s in report.cis_scores:
            assert s.failed_controls <= s.total_controls
            assert s.passed_controls == s.total_controls - s.failed_controls

    def test_empty_findings_yields_perfect_score(self):
        report = generate_compliance_report([])
        for s in report.cis_scores:
            assert s.score_percent == 100.0
            assert s.failed_controls == 0

    def test_all_findings_failed_yields_zero_score(self):
        # Trigger every KB entry — every benchmark control is failed
        all_fids = [{"id": fid, "severity": "high", "category": "x"} for fid in HARDENING_KB]
        report = generate_compliance_report(all_fids)
        for s in report.cis_scores:
            assert s.score_percent == 0.0

    def test_failed_control_ids_populated(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        rhel = next(s for s in report.cis_scores if s.benchmark == "CIS-RHEL9")
        assert rhel.failed_control_ids
        # LSEC-SELINUX-001 maps to CIS-RHEL9 1.6.1.1
        assert "CIS-RHEL9 1.6.1.1" in rhel.failed_control_ids

    def test_cis_status_per_control(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        statuses = {c.control_id: c.status for c in report.cis_controls}
        assert statuses["CIS-RHEL9 1.6.1.1"] == "fail"
        # A KB control not triggered by linux_findings should pass
        assert statuses["CIS-RHEL9 1.4.3"] == "pass"


# ---- remediation roadmap -----------------------------------------------------

class TestRemediationRoadmap:
    def test_three_phases_present(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        phases = {p.phase for p in report.remediation_roadmap}
        assert phases == {"immediate", "short_term", "long_term"}

    def test_critical_and_high_in_immediate(self, windows_findings):
        report = generate_compliance_report(windows_findings)
        immediate = next(p for p in report.remediation_roadmap if p.phase == "immediate")
        assert "WSEC-DEF-001" in immediate.finding_ids
        assert "WSEC-FW-001" in immediate.finding_ids

    def test_medium_in_short_term(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        short_term = next(p for p in report.remediation_roadmap if p.phase == "short_term")
        assert "LSEC-AUDITD-001" in short_term.finding_ids

    def test_low_and_info_in_long_term(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        long_term = next(p for p in report.remediation_roadmap if p.phase == "long_term")
        assert "LSEC-KERNEL-003" in long_term.finding_ids

    def test_phase_totals_sum_to_matched(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        total = sum(p.total for p in report.remediation_roadmap)
        assert total == len(mixed_findings)

    def test_phase_has_label(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        for p in report.remediation_roadmap:
            assert isinstance(p.label, str) and p.label


# ---- summary -----------------------------------------------------------------

class TestSummary:
    def test_summary_has_overall_score(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        assert "overall_cis_score" in report.summary
        assert 0.0 <= report.summary["overall_cis_score"] <= 100.0

    def test_summary_has_stig_counts(self, linux_findings):
        report = generate_compliance_report(linux_findings)
        assert "stig_non_compliant" in report.summary
        assert "stig_not_assessed" in report.summary
        assert report.summary["stig_non_compliant"] >= 1

    def test_summary_has_total_findings(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        assert report.summary["total_findings"] == len(mixed_findings)


# ---- edge cases --------------------------------------------------------------

class TestEdgeCases:
    def test_empty_findings(self):
        report = generate_compliance_report([])
        assert report.summary["total_findings"] == 0
        # STIG entries still come from the KB
        assert len(report.stig) > 0
        for s in report.stig:
            assert s.status == "not_assessed"

    def test_finding_without_id_skipped(self):
        report = generate_compliance_report([{"severity": "high"}])
        assert report.summary["total_findings"] == 0

    def test_unknown_finding_id_ignored_gracefully(self):
        report = generate_compliance_report([
            {"id": "LSEC-SELINUX-001", "severity": "high", "category": "selinux"},
            {"id": "DOES-NOT-EXIST-999", "severity": "low", "category": "x"},
        ])
        # Unknown finding has no KB entry -> not counted in matched, but counted in total
        assert report.summary["total_findings"] == 2
        assert report.summary["matched_findings"] == 1


# ---- serialization & markdown ------------------------------------------------

class TestSerialization:
    def test_to_dict_roundtrip(self, mixed_findings):
        report = generate_compliance_report(mixed_findings)
        d = report.to_dict()
        assert "stig" in d
        assert "nist_800_53" in d
        assert "cis_scores" in d
        assert "cis_controls" in d
        assert "remediation_roadmap" in d
        assert "summary" in d
        assert isinstance(d["stig"], list)
        assert isinstance(d["cis_scores"], list)

    def test_markdown_has_all_sections(self, mixed_findings):
        md = format_compliance_markdown(generate_compliance_report(mixed_findings))
        assert "# Compliance Report" in md
        assert "## STIG Compliance Status" in md
        assert "## NIST SP 800-53 Mapping" in md
        assert "## CIS Benchmark Scores" in md
        assert "## Remediation Roadmap" in md

    def test_markdown_empty_report(self):
        md = format_compliance_markdown(generate_compliance_report([]))
        assert "# Compliance Report" in md
        assert "No audit findings" in md

    def test_markdown_lists_stig_ids(self, linux_findings):
        md = format_compliance_markdown(generate_compliance_report(linux_findings))
        assert "RHEL-09-651015" in md

    def test_markdown_lists_nist_controls(self, linux_findings):
        md = format_compliance_markdown(generate_compliance_report(linux_findings))
        assert "AC-3" in md

    def test_markdown_lists_cis_scores(self, linux_findings):
        md = format_compliance_markdown(generate_compliance_report(linux_findings))
        assert "CIS-RHEL9" in md
        assert "%" in md
