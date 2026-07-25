"""Structural pin for the Operational Discipline (OD.1-3) + Root Cause
Escalation sections of AGENTS.md.

These rules codify the recurring failure modes observed across sessions:
  * OD.1 — reporting intermediate progress (build running, tag pushed, CI
    pending) as if it were completion.
  * OD.2 — substituting / optimizing / "improving" a measurable user
    requirement instead of meeting it exactly.
  * Root Cause Escalation (3-Strike Rule) — patching symptoms after repeated
    CI failures of the same class instead of fixing the systemic cause.

The tests verify the sections are PRESENT and contain their load-bearing
phrases. They do not exercise runtime behavior — the rules are proactive
prompt-layer instructions. If a section is removed or a key phrase is
edited away, these tests fail loudly so the guardrail cannot quietly
regress.

Run:  make test-specific TESTFILE='tests/unit/test_operational_discipline_sections'
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
AGENTS_MD = ROOT / "AGENTS.md"


@pytest.fixture(scope="module")
def agents_md_content() -> str:
    """Read AGENTS.md once for all assertions in this module."""
    assert AGENTS_MD.exists(), "AGENTS.md must exist at repo root"
    return AGENTS_MD.read_text()


def _section(content: str, heading: str) -> str:
    """Extract the body of a markdown section (heading up to next ## of same depth)."""
    marker = f"## {heading}"
    start = content.find(marker)
    assert start != -1, f"section '{heading}' not found in AGENTS.md"
    body_start = content.find("\n", start) + 1
    next_section = content.find("\n## ", body_start)
    if next_section == -1:
        return content[body_start:]
    return content[body_start:next_section]


class TestOperationalDisciplineSectionExists:
    """The umbrella Operational Discipline Rules section must exist."""

    def test_umbrella_heading_present(self, agents_md_content: str) -> None:
        assert (
            "## CRITICAL: Operational Discipline Rules" in agents_md_content
        ), (
            "AGENTS.md must have a 'CRITICAL: Operational Discipline Rules' "
            "section — it groups the OD.* sub-rules that prevent the recurring "
            "stop-and-report / substitute-and-optimize failure modes."
        )


class TestOD1IntermediateProgressNotCompletion:
    """OD.1 — Intermediate Progress Is Not Completion."""

    def test_heading_present(self, agents_md_content: str) -> None:
        assert "OD.1 — Intermediate Progress Is Not Completion" in agents_md_content, (
            "AGENTS.md must have an 'OD.1 — Intermediate Progress Is Not "
            "Completion' sub-rule — reporting a build/tag/CI-pending state as a "
            "stopping point is a documented premature-stop pattern."
        )

    def test_completion_gate_referenced(self, agents_md_content: str) -> None:
        body = _section(
            agents_md_content, "CRITICAL: Operational Discipline Rules"
        )
        assert "make verify-release-completeness" in body, (
            "OD.1 must name 'make verify-release-completeness TAG=<tag>' as the "
            "single completion criterion — without a named gate the rule is "
            "unverifiable and regresses to assertion-based 'done' claims."
        )

    @pytest.mark.parametrize(
        "phrase",
        ["build is running", "tag is pushed", "CI is pending"],
    )
    def test_non_stopping_states_enumerated(
        self, agents_md_content: str, phrase: str
    ) -> None:
        # OD.1 body is small; check the whole umbrella section for the
        # enumerated non-completion states (some appear in OD.3 etc.).
        body = _section(
            agents_md_content, "CRITICAL: Operational Discipline Rules"
        )
        assert phrase in body, (
            f"Operational Discipline section must enumerate '{phrase}' as a "
            f"non-completion state — naming the specific intermediate states is "
            f"what makes the rule pattern-matchable."
        )


class TestOD2FollowExplicitInstructionsExactly:
    """OD.2 — Follow Explicit Instructions Exactly."""

    def test_heading_present(self, agents_md_content: str) -> None:
        assert "OD.2 — Follow Explicit Instructions Exactly" in agents_md_content, (
            "AGENTS.md must have an 'OD.2 — Follow Explicit Instructions "
            "Exactly' sub-rule — substituting or optimizing a measurable user "
            "requirement is a documented override-the-user failure mode."
        )

    @pytest.mark.parametrize(
        "phrase",
        ["measurable requirement", "Do not optimize", "substitute"],
    )
    def test_key_phrase_present(
        self, agents_md_content: str, phrase: str
    ) -> None:
        body = _section(
            agents_md_content, "CRITICAL: Operational Discipline Rules"
        )
        assert phrase in body, (
            f"Operational Discipline section must contain '{phrase}' — without "
            f"it OD.2 loses its force and the agent can rationalize substitution."
        )

    def test_concrete_examples_present(self, agents_md_content: str) -> None:
        body = _section(
            agents_md_content, "CRITICAL: Operational Discipline Rules"
        )
        # The rule must carry at least one concrete measurable example
        # (word count / artifact count) so it is not abstract.
        assert "16000 words" in body or "12 artifacts" in body, (
            "OD.2 must include a concrete measurable example (e.g. '16000 "
            "words' or '12 artifacts') — abstract rules without examples are "
            "ignored under time pressure."
        )


class TestRootCauseEscalationSection:
    """## CRITICAL: Root Cause Escalation (3-Strike Rule)."""

    def test_heading_present(self, agents_md_content: str) -> None:
        assert (
            "## CRITICAL: Root Cause Escalation (3-Strike Rule)"
            in agents_md_content
        ), (
            "AGENTS.md must have a 'CRITICAL: Root Cause Escalation (3-Strike "
            "Rule)' section — patching symptoms after 3 CI failures of the same "
            "class is a documented escalation failure mode."
        )

    def test_three_strike_threshold(self, agents_md_content: str) -> None:
        body = _section(
            agents_md_content, "CRITICAL: Root Cause Escalation (3-Strike Rule)"
        )
        assert "third time" in body, (
            "Root Cause Escalation must name the 'third time' threshold — "
            "without an explicit strike count the rule never triggers."
        )

    @pytest.mark.parametrize(
        "step",
        [
            "STOP patching symptoms",
            "SYSTEMIC dependency",
            "structural test",
        ],
    )
    def test_procedure_step_present(
        self, agents_md_content: str, step: str
    ) -> None:
        body = _section(
            agents_md_content, "CRITICAL: Root Cause Escalation (3-Strike Rule)"
        )
        assert step in body, (
            f"Root Cause Escalation procedure must include '{step}' — each "
            f"step is load-bearing; removing one collapses the rule into "
            f"ad-hoc symptom-patching."
        )

    @pytest.mark.parametrize(
        "forbidden",
        [
            "Increasing timeout",
            "Dropping Python versions",
            "Making artifacts optional",
        ],
    )
    def test_forbidden_symptom_fix_listed(
        self, agents_md_content: str, forbidden: str
    ) -> None:
        body = _section(
            agents_md_content, "CRITICAL: Root Cause Escalation (3-Strike Rule)"
        )
        assert forbidden in body, (
            f"Root Cause Escalation must list '{forbidden}' as a forbidden "
            f"symptom-patch — enumerating the concrete anti-patterns is what "
            f"distinguishes the rule from a generic 'fix root causes' platitude."
        )
