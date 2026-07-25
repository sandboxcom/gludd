"""OD.4-OD.10 + DC.1-DC.2 + RP.24 — full operational discipline section coverage.

Verifies that every Operational Discipline rule (OD.1 through OD.10) plus the
CI Wait Productivity (DC.1) and Polling CI Is Not Work (DC.2) sections are
present in AGENTS.md with their canonical headings.

See AGENTS.md "Operational Discipline Rules (Session 52 Codification)" and the
"CI Wait Productivity (DC.1)" / "Polling CI Is Not Work (DC.2)" sections.
"""

from __future__ import annotations

import re
from pathlib import Path

AGENTS_MD = Path(__file__).resolve().parents[2] / "AGENTS.md"

OD_RULES: list[tuple[str, str]] = [
    ("OD.1", "Intermediate Progress Is Not Completion"),
    ("OD.2", "Follow Explicit Instructions Exactly"),
    ("OD.3", "CI Is Fire-and-Forget"),
    ("OD.4", "No Text-Only Responses With Pending Work"),
    ("OD.5", "Answer Direct Questions Directly First"),
    ("OD.6", "Don't Rationalize Stops"),
    ("OD.7", "Don't Override User Instructions"),
    ("OD.8", "Don't Make Artifacts Optional"),
    ("OD.9", "Don't Push Broken Code Without Lint"),
    ("OD.10", "No CI Polling as Pretend Work"),
]

DC_SECTIONS: list[tuple[str, str]] = [
    ("DC.1", "CI Wait Productivity"),
    ("DC.2", "Polling CI Is Not Work"),
]


def _read_agents_md() -> str:
    return AGENTS_MD.read_text(encoding="utf-8")


def test_agents_md_exists() -> None:
    """AGENTS.md must be readable."""
    assert AGENTS_MD.is_file(), f"AGENTS.md not found at {AGENTS_MD}"


def test_operational_discipline_umbrella_section() -> None:
    """The umbrella 'Operational Discipline Rules' section heading must exist."""
    content = _read_agents_md()
    assert "Operational Discipline Rules" in content, (
        "AGENTS.md must contain the 'Operational Discipline Rules' section heading."
    )


def test_od_rules_present() -> None:
    """Each OD rule (OD.1 through OD.10) must appear with its title."""
    content = _read_agents_md()
    missing: list[str] = []
    for rule_id, title in OD_RULES:
        heading = f"### {rule_id} - {title}"
        if heading not in content:
            em_heading = f"### {rule_id} \u2014 {title}"
            if em_heading not in content:
                missing.append(f"{rule_id} - {title}")
    assert not missing, (
        "AGENTS.md is missing OD rule headings: " + "; ".join(missing)
    )


def test_dc_sections_present() -> None:
    """DC.1 (CI Wait Productivity) and DC.2 (Polling CI Is Not Work) must exist."""
    content = _read_agents_md()
    missing: list[str] = []
    for section_id, title in DC_SECTIONS:
        pattern = rf"##\s+CRITICAL:\s+{re.escape(title)}\s*\({re.escape(section_id)}\)"
        if not re.search(pattern, content):
            missing.append(f"{section_id} - {title}")
    assert not missing, (
        "AGENTS.md is missing DC sections: " + "; ".join(missing)
    )


def test_dc1_dispatch_examples() -> None:
    """DC.1 must enumerate concrete dispatch examples for CI wait periods."""
    content = _read_agents_md()
    dc1_block = _extract_section(content, "CI Wait Productivity (DC.1)")
    assert dc1_block is not None, "DC.1 section not found"
    for phrase in ("fix tests", "structural tests", "docs"):
        assert phrase in dc1_block, (
            f"DC.1 section must mention '{phrase}' as a dispatch example."
        )


def test_dc1_zero_subagents_forbidden() -> None:
    """DC.1 must explicitly forbid 0 subagents during CI waits."""
    content = _read_agents_md()
    dc1_block = _extract_section(content, "CI Wait Productivity (DC.1)")
    assert dc1_block is not None, "DC.1 section not found"
    assert "0 subagents" in dc1_block, (
        "DC.1 must call out '0 subagents during CI wait is a policy violation'."
    )


def test_dc2_three_poll_threshold() -> None:
    """DC.2 must specify the 3-times-in-a-row polling threshold."""
    content = _read_agents_md()
    dc2_block = _extract_section(content, "Polling CI Is Not Work (DC.2)")
    assert dc2_block is not None, "DC.2 section not found"
    assert "3 times" in dc2_block, (
        "DC.2 must name the 'more than 3 times in a row' threshold."
    )


def test_rp24_ci_wait_productivity_covered() -> None:
    """RP.24 (CI Wait Productivity dispatch guide) is satisfied by DC.1.

    RP.24 in TASKS.md asks for an AGENTS.md section with concrete dispatch
    examples for CI wait periods. DC.1 fulfills that requirement.
    """
    content = _read_agents_md()
    assert re.search(
        r"##\s+CRITICAL:\s+CI Wait Productivity\s*\(DC\.1\)", content
    ), (
        "RP.24 requires the 'CI Wait Productivity (DC.1)' section in AGENTS.md."
    )


def _extract_section(content: str, heading_token: str) -> str | None:
    """Extract the body of a markdown section identified by a heading token.

    Returns the heading line plus all body lines until the next heading of
    the same or higher level, or None if the token is not found.
    """
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if heading_token in line and line.lstrip().startswith("#"):
            body: list[str] = [line]
            heading_level = len(line) - len(line.lstrip("#"))
            for follow in lines[idx + 1 :]:
                stripped = follow.lstrip()
                if stripped.startswith("#"):
                    follow_level = len(follow) - len(follow.lstrip("#"))
                    if follow_level <= heading_level:
                        break
                body.append(follow)
            return "\n".join(body)
    return None
