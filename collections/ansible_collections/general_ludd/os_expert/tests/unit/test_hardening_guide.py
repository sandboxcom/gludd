"""Tests for the OS hardening recommendation engine (module_utils/hardening_guide.py).

TDD: written before the implementation. Covers recommendation generation,
severity prioritization, completeness of the knowledge base against every
audit finding ID in the collection, markdown rendering, and edge cases.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent
if str(COLLECTION_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTION_ROOT))

from module_utils.hardening_guide import (  # noqa: E402
    HARDENING_KB,
    SEVERITY_RANK,
    format_markdown,
    generate_guide,
)

ROLES_DIR = COLLECTION_ROOT / "roles"


def _extract_finding_ids_from_audits() -> set[str]:
    """Parse every *_audit.py in the collection and collect finding IDs."""
    ids: set[str] = set()
    id_re = re.compile(r'"id":\s*"([A-Z]+(?:-[A-Z]+)+-\d+)"')
    for audit_file in ROLES_DIR.glob("*/files/*_audit.py"):
        text = audit_file.read_text(encoding="utf-8")
        ids.update(id_re.findall(text))
    return ids


# ---- fixtures ----------------------------------------------------------------

@pytest.fixture
def linux_findings() -> list[dict]:
    return [
        {"id": "LSEC-SELINUX-001", "severity": "high", "category": "selinux",
         "description": "SELinux is disabled"},
        {"id": "LSEC-KERNEL-001", "severity": "high", "category": "kernel",
         "description": "ASLR not fully enabled"},
        {"id": "LSEC-PAM-002", "severity": "medium", "category": "pam",
         "description": "pam_faillock not configured"},
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


# ---- knowledge base completeness --------------------------------------------

class TestKnowledgeBaseCompleteness:
    def test_every_audit_finding_id_has_a_hardening_entry(self):
        """The KB must cover 100% of finding IDs emitted by the auditors."""
        audit_ids = _extract_finding_ids_from_audits()
        missing = audit_ids - set(HARDENING_KB)
        assert not missing, f"Finding IDs missing from HARDENING_KB: {sorted(missing)}"

    def test_kb_entry_shape(self):
        for fid, tmpl in HARDENING_KB.items():
            assert isinstance(tmpl["title"], str) and tmpl["title"], f"{fid}: empty title"
            assert isinstance(tmpl["rationale"], str) and tmpl["rationale"], f"{fid}: empty rationale"
            assert isinstance(tmpl["commands"], list) and tmpl["commands"], f"{fid}: no commands"
            for cmd in tmpl["commands"]:
                assert isinstance(cmd, str) and cmd.strip(), f"{fid}: blank command"
            assert "verification" in tmpl, f"{fid}: missing verification"
            assert "references" in tmpl, f"{fid}: missing references"


# ---- generate_guide ----------------------------------------------------------

class TestGenerateGuide:
    def test_returns_recommendation_for_each_known_finding(self, linux_findings):
        guide = generate_guide(linux_findings)
        assert len(guide.recommendations) == len(linux_findings)

    def test_unknown_finding_ids_captured_in_summary(self):
        findings = [
            {"id": "LSEC-SELINUX-001", "severity": "high", "category": "selinux"},
            {"id": "UNKNOWN-999", "severity": "low", "category": "misc"},
        ]
        guide = generate_guide(findings)
        assert len(guide.recommendations) == 1
        assert guide.summary["unmapped_finding_ids"] == ["UNKNOWN-999"]

    def test_recommendations_sorted_by_severity(self, windows_findings):
        guide = generate_guide(windows_findings)
        sevs = [r.severity for r in guide.recommendations]
        ranks = [SEVERITY_RANK[s] for s in sevs]
        assert ranks == sorted(ranks), "recommendations not severity-ordered"

    def test_critical_before_medium(self, windows_findings):
        guide = generate_guide(windows_findings)
        assert guide.recommendations[0].severity == "critical"
        assert guide.recommendations[-1].severity == "medium"

    def test_summary_counts_by_severity(self, linux_findings):
        guide = generate_guide(linux_findings)
        by_sev = guide.summary["by_severity"]
        assert by_sev.get("high") == 2
        assert by_sev.get("medium") == 1
        assert by_sev.get("low") == 1

    def test_total_findings_recorded(self, windows_findings):
        guide = generate_guide(windows_findings)
        assert guide.summary["total_findings"] == len(windows_findings)

    def test_reboot_flag_aggregated(self, linux_findings):
        guide = generate_guide(linux_findings)
        assert isinstance(guide.summary["requires_reboot"], bool)

    def test_recommendation_carries_commands_and_refs(self, linux_findings):
        guide = generate_guide(linux_findings)
        for rec in guide.recommendations:
            assert isinstance(rec.commands, list) and rec.commands
            assert isinstance(rec.references, list)
            assert rec.verification

    def test_recommendation_preserves_finding_context(self):
        f = {"id": "LSEC-SELINUX-001", "severity": "high", "category": "selinux"}
        rec = generate_guide([f]).recommendations[0]
        assert rec.finding_id == "LSEC-SELINUX-001"
        assert rec.severity == "high"
        assert rec.category == "selinux"


# ---- edge cases --------------------------------------------------------------

class TestEdgeCases:
    def test_empty_findings_returns_empty_guide(self):
        guide = generate_guide([])
        assert guide.recommendations == []
        assert guide.summary["total_findings"] == 0
        assert guide.summary["matched_recommendations"] == 0

    def test_finding_without_id_is_skipped(self):
        guide = generate_guide([{"severity": "high", "category": "x"}])
        assert guide.recommendations == []

    def test_duplicate_finding_ids_produce_duplicate_recs(self):
        f = {"id": "LSEC-AUDITD-001", "severity": "medium", "category": "auditd"}
        guide = generate_guide([f, f])
        assert len(guide.recommendations) == 2

    def test_change_risk_is_valid_enum(self, linux_findings):
        guide = generate_guide(linux_findings)
        for rec in guide.recommendations:
            assert rec.change_risk in ("low", "medium", "high")


# ---- serialization -----------------------------------------------------------

class TestSerialization:
    def test_to_dict_roundtrip(self, windows_findings):
        guide = generate_guide(windows_findings)
        d = guide.to_dict()
        assert "summary" in d
        assert "recommendations" in d
        assert len(d["recommendations"]) == len(windows_findings)
        assert isinstance(d["recommendations"][0]["commands"], list)

    def test_markdown_has_priority_header(self, linux_findings):
        md = format_markdown(generate_guide(linux_findings))
        assert "# OS Hardening Recommendations" in md

    def test_markdown_lists_each_recommendation(self, windows_findings):
        md = format_markdown(generate_guide(windows_findings))
        for fid in ("WSEC-DEF-001", "WSEC-FW-001", "WSEC-PW-002"):
            assert fid in md
        assert "```" in md  # command code blocks present

    def test_markdown_empty_guide(self):
        md = format_markdown(generate_guide([]))
        assert "No hardening actions required" in md


# ---- CIS Benchmark mapping ---------------------------------------------------

# Machine-parseable CIS control id format: "CIS-<Benchmark> <section>"
# e.g. "CIS-RHEL9 1.6.1.1", "CIS-Win11 18.9.1", "CIS-Ubuntu 4.4"
CIS_ID_RE = re.compile(r"^CIS-\S+ \S+$")


class TestCISMapping:
    def test_every_kb_entry_has_cis_controls(self):
        """Every hardening recipe must carry >=1 structured CIS control id."""
        missing = [fid for fid, t in HARDENING_KB.items() if not t.get("cis_controls")]
        assert not missing, f"KB entries missing cis_controls: {sorted(missing)}"

    def test_cis_controls_are_well_formed(self):
        """Each cis_controls entry must match the CIS-<Benchmark> <section> shape."""
        bad: list[str] = []
        for fid, tmpl in HARDENING_KB.items():
            for cid in tmpl.get("cis_controls", []):
                if not CIS_ID_RE.match(cid):
                    bad.append(f"{fid}: {cid!r}")
        assert not bad, f"Malformed CIS control ids: {bad}"

    def test_cis_controls_are_unique_per_entry(self):
        for fid, tmpl in HARDENING_KB.items():
            cids = tmpl.get("cis_controls", [])
            assert len(cids) == len(set(cids)), f"{fid}: duplicate cis_controls"

    def test_linux_findings_map_to_rhel_or_ubuntu(self):
        """LSEC-* entries must reference CIS-RHEL9 or CIS-Ubuntu benchmarks."""
        for fid, tmpl in HARDENING_KB.items():
            if not fid.startswith("LSEC-"):
                continue
            assert any(
                c.startswith(("CIS-RHEL", "CIS-Ubuntu"))
                for c in tmpl["cis_controls"]
            ), f"{fid}: Linux finding must map to RHEL/Ubuntu CIS benchmark"

    def test_windows_findings_map_to_win11(self):
        """WSEC-* entries must reference the CIS-Win11 benchmark."""
        for fid, tmpl in HARDENING_KB.items():
            if not fid.startswith("WSEC-"):
                continue
            assert any(
                c.startswith("CIS-Win") for c in tmpl["cis_controls"]
            ), f"{fid}: Windows finding must map to CIS-Win benchmark"

    def test_recommendation_carries_cis_controls(self, linux_findings):
        guide = generate_guide(linux_findings)
        for rec in guide.recommendations:
            assert isinstance(rec.cis_controls, list)
            assert rec.cis_controls, f"{rec.finding_id}: empty cis_controls"
            for cid in rec.cis_controls:
                assert CIS_ID_RE.match(cid), f"{rec.finding_id}: bad CIS id {cid!r}"

    def test_cis_controls_roundtrip_through_to_dict(self, windows_findings):
        d = generate_guide(windows_findings).to_dict()
        for rec in d["recommendations"]:
            assert "cis_controls" in rec
            assert rec["cis_controls"]

    def test_markdown_renders_cis_section(self, windows_findings):
        md = format_markdown(generate_guide(windows_findings))
        assert "CIS Benchmark" in md
        assert "CIS-Win" in md

    def test_known_specific_mappings(self):
        """Spot-check canonical mappings to guard against drift."""
        assert HARDENING_KB["LSEC-SELINUX-001"]["cis_controls"] == ["CIS-RHEL9 1.6.1.1"]
        assert HARDENING_KB["LSEC-AUDITD-001"]["cis_controls"] == ["CIS-RHEL9 4.1.1.1"]
        assert HARDENING_KB["WSEC-FW-001"]["cis_controls"] == ["CIS-Win11 9.1.1"]
        assert HARDENING_KB["WSEC-LOCKOUT-001"]["cis_controls"] == ["CIS-Win11 1.2.1"]
