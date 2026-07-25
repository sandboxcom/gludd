"""OD.1-OD.10 — Operational Discipline Rules (Session 52 Codification).

Verifies each Operational Discipline rule (OD.1 through OD.10) is present in
AGENTS.md as a prompt-layer guardrail. These rules were codified after a
session where the agent repeatedly stopped on intermediate progress, polled
CI as pretend work, and rationalized stops. The tests pin the load-bearing
section headings so a regression that strips them is caught.

See AGENTS.md "Operational Discipline Rules (Session 52 Codification)".
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
AGENTS_MD = ROOT / "AGENTS.md"


@pytest.fixture(scope="module")
def agents_src():
    if not AGENTS_MD.exists():
        pytest.fail("AGENTS.md must exist at the repo root.")
    return AGENTS_MD.read_text()


OD_RULES = [
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


class TestOperationalDisciplineSectionExists:
    """The parent section heading must be present in AGENTS.md."""

    def test_section_heading_present(self, agents_src):
        assert "Operational Discipline Rules" in agents_src, (
            "AGENTS.md must contain the 'Operational Discipline Rules' section heading."
        )

    def test_session_52_codification_present(self, agents_src):
        assert "Session 52 Codification" in agents_src, (
            "AGENTS.md must reference 'Session 52 Codification' so the rule origin "
            "is traceable to the incident that produced it."
        )


class TestEachRulePresent:
    """Each OD rule (OD.1 through OD.10) must appear with its title."""

    @pytest.mark.parametrize("rule_id,rule_title", OD_RULES, ids=[r[0] for r in OD_RULES])
    def test_rule_id_present(self, agents_src, rule_id, rule_title):
        assert rule_id in agents_src, (
            f"AGENTS.md must contain rule id '{rule_id}' ({rule_title})."
        )

    @pytest.mark.parametrize("rule_id,rule_title", OD_RULES, ids=[r[0] for r in OD_RULES])
    def test_rule_title_present(self, agents_src, rule_id, rule_title):
        assert rule_title in agents_src, (
            f"AGENTS.md must contain the '{rule_title}' heading under {rule_id}."
        )

    @pytest.mark.parametrize("rule_id,rule_title", OD_RULES, ids=[r[0] for r in OD_RULES])
    def test_rule_header_line(self, agents_src, rule_id, rule_title):
        # The heading should be formatted as `### <id> — <title>`.
        expected = f"### {rule_id} "
        assert expected in agents_src, (
            f"AGENTS.md must format {rule_id} as a level-3 heading "
            f"(expected '{expected}' prefix for '{rule_title}')."
        )


class TestRuleCount:
    """All 10 rules must be present — no partial regression."""

    def test_all_ten_rules_present(self, agents_src):
        for n in range(1, 11):
            assert f"OD.{n}" in agents_src, (
                f"AGENTS.md is missing rule OD.{n} — all 10 rules must be present."
            )
