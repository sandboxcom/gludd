"""CRITICAL: Pipeline Completion Is The Primary Objective.

Verifies the "Pipeline Completion Is The Primary Objective" section is present
in AGENTS.md as a prompt-layer guardrail. This section was codified after
multiple sessions where the agent spent days adding structural tests and
guardrails while the release pipeline stayed red or unpushed. The user's
explicit feedback: the CI pipeline build has NOT been the priority for DAYS.

These tests pin the load-bearing section heading and key phrases so a
regression that strips them (or weakens the priority) is caught at gate time.

See AGENTS.md "CRITICAL: Pipeline Completion Is The Primary Objective".
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
AGENTS_MD = ROOT / "AGENTS.md"

SECTION_HEADING = "CRITICAL: Pipeline Completion Is The Primary Objective"

# Key phrases that encode the load-bearing rules. Each must appear in the
# section body; stripping any one of them weakens the guardrail.
KEY_PHRASES = [
    "#1 priority",
    "FIRST dispatch in every wave MUST be pipeline-focused",
    "push unpushed commits",
    "check CI verdict",
    "fix CI failures",
    "cut the release",
    "SECONDARY priority",
    "NEVER consume more than 50% of the dispatch wave",
    "Check push status at session start",
    "pushing them is the",
    "FIRST action",
    "CI is pending",
    "never a stop",
    "verify-release-completeness",
    "all 12 asset",
    "NOT done",
]


@pytest.fixture(scope="module")
def agents_src():
    if not AGENTS_MD.exists():
        pytest.fail("AGENTS.md must exist at the repo root.")
    return AGENTS_MD.read_text()


@pytest.fixture(scope="module")
def section_src(agents_src):
    """Extract just the target section body (heading up to the next ## heading).

    Whitespace is normalized (all runs of spaces/newlines collapsed to a single
    space) so phrase-matching is robust against markdown line-wrapping — the
    semantic content is what matters, not the column width.
    """
    import re

    idx = agents_src.find(SECTION_HEADING)
    if idx < 0:
        pytest.fail(
            f"AGENTS.md must contain the section heading '{SECTION_HEADING}'."
        )
    # Slice from the heading to the next top-level (## ) heading that follows it.
    after = agents_src[idx + len(SECTION_HEADING):]
    next_heading = after.find("\n## ")
    if next_heading < 0:
        # Last section in the file — take the rest.
        section = agents_src[idx:]
    else:
        section = agents_src[idx:idx + len(SECTION_HEADING) + next_heading]
    return re.sub(r"\s+", " ", section)


class TestSectionHeadingPresent:
    """The section heading must exist as a top-level CRITICAL section."""

    def test_heading_present(self, agents_src):
        assert SECTION_HEADING in agents_src, (
            f"AGENTS.md must contain the section heading '{SECTION_HEADING}' "
            "— the prompt-layer guardrail against deprioritizing the pipeline."
        )

    def test_heading_is_top_level(self, agents_src):
        """The heading must be a level-2 (##) heading, not buried under another section."""
        # Look for '## <HEADING>' at start of a line.
        assert f"\n## {SECTION_HEADING}" in agents_src, (
            f"'{SECTION_HEADING}' must be a top-level (##) heading in AGENTS.md, "
            "not a subsection."
        )


class TestKeyPhrasesPresent:
    """Each load-bearing phrase must appear within the section body."""

    @pytest.mark.parametrize("phrase", KEY_PHRASES, ids=[p[:30] for p in KEY_PHRASES])
    def test_phrase_in_section(self, section_src, phrase):
        assert phrase in section_src, (
            f"AGENTS.md 'Pipeline Completion Is The Primary Objective' section "
            f"must contain the phrase: {phrase!r}. Stripping it weakens the "
            "pipeline-priority guardrail."
        )


class TestAntiPatternsPresent:
    """The anti-patterns list must call out the specific failure modes."""

    EXPECTED_ANTI_PATTERNS = [
        "structural-test subagents while the pipeline is red",
        "new enforcement plugin while commits sit unpushed",
        "documentation while CI is failing",
        "CI is pending",
        "unrelated feature work",
        "0 pipeline-focused dispatches",
    ]

    @pytest.mark.parametrize(
        "phrase", EXPECTED_ANTI_PATTERNS, ids=[p[:30] for p in EXPECTED_ANTI_PATTERNS]
    )
    def test_anti_pattern_listed(self, section_src, phrase):
        assert phrase in section_src, (
            f"AGENTS.md pipeline-priority section must list anti-pattern: "
            f"{phrase!r}. The anti-patterns list is what makes the rule "
            "actionable — without it, the agent can rationalize violations."
        )


class TestEnforcementLayerReferenced:
    """The section must name its enforcement layers (3-layer guardrail pattern)."""

    def test_references_structural_test(self, section_src):
        assert "test_pipeline_priority.py" in section_src, (
            "AGENTS.md pipeline-priority section must reference its structural "
            "test file (tests/unit/test_pipeline_priority.py) — the test layer "
            "of the 3-layer guardrail."
        )

    def test_references_operational_discipline(self, section_src):
        assert "OD.1" in section_src and "OD.3" in section_src, (
            "AGENTS.md pipeline-priority section must cross-reference the "
            "Operational Discipline rules (OD.1, OD.3) that reinforce it."
        )


class TestSecondaryWorkCap:
    """The 50% cap on secondary work is the load-bearing ratio rule."""

    def test_fifty_percent_cap_stated(self, section_src):
        # The rule must state the cap both as prose ("50%") and as the
        # concrete wave allocation ("at least 5" of 10).
        assert "50%" in section_src, (
            "AGENTS.md must state the 50% cap on secondary work explicitly."
        )
        assert "at least 5" in section_src, (
            "AGENTS.md must translate the 50% cap into a concrete slot count "
            "(at least 5 of 10) so it is unambiguous."
        )


class TestReleaseCompletenessGate:
    """The release-done definition must require verify-release-completeness."""

    def test_verify_release_completeness_required(self, section_src):
        assert "make verify-release-completeness TAG=<tag>" in section_src, (
            "AGENTS.md must name 'make verify-release-completeness TAG=<tag>' as "
            "the release-done gate — not a tag push, not a green CI run."
        )

    def test_exits_zero_required(self, section_src):
        assert "exits 0" in section_src, (
            "AGENTS.md must specify that verify-release-completeness must "
            "'exits 0' — a non-zero exit is not done."
        )
