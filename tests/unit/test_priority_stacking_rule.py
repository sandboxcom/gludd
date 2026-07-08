"""Structural pin for the Priority Stacking (AND not OR) meta-rule.

This rule (codified in AGENTS.md) prevents the recurring failure mode where the
agent interprets a new user directive as REPLACING existing objectives (like the
10-agent multitasking floor) when it should be ADDING to the priority stack.

These tests verify the rule is PRESENT and COMPLETE in AGENTS.md — they do not
exercise runtime behavior, because the rule is a proactive prompt-layer
instruction (not a plugin hook). If the section is removed or key phrases are
edited away, these tests fail loudly so the guardrail cannot quietly regress.

Run:  make test TESTFILE=tests/unit/test_priority_stacking_rule.py
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


class TestSectionExists:
    """The Priority Stacking section must exist as a top-level CRITICAL policy."""

    def test_heading_present(self, agents_md_content: str) -> None:
        assert "## CRITICAL: Priority Stacking (AND not OR)" in agents_md_content, (
            "AGENTS.md must have a 'Priority Stacking (AND not OR)' section — "
            "this is the binding meta-rule preventing substitutive interpretation "
            "of new instructions."
        )

    def test_section_not_empty(self, agents_md_content: str) -> None:
        body = _section(agents_md_content, "CRITICAL: Priority Stacking (AND not OR)")
        assert len(body.strip()) > 200, (
            "Priority Stacking section must be a substantive policy, not a stub."
        )


class TestCorePhrases:
    """The rule must contain the load-bearing phrases that make it actionable."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "ADDITIVELY",
            "AND not OR",
            "SUBSTITUTIVELY",
            "first dispatch",
            "first check",
            "First follow-up",
            "Multitasking preserved",
        ],
    )
    def test_key_phrase_present(self, agents_md_content: str, phrase: str) -> None:
        body = _section(agents_md_content, "CRITICAL: Priority Stacking (AND not OR)")
        assert phrase in body, (
            f"Priority Stacking section must contain '{phrase}' — "
            f"without it the rule is incomplete and the guardrail regresses."
        )


class TestWorkedExamplesTable:
    """The anti-pattern examples table must exist so agents can pattern-match."""

    def test_table_present(self, agents_md_content: str) -> None:
        body = _section(agents_md_content, "CRITICAL: Priority Stacking (AND not OR)")
        # The worked-examples table has a header row with WRONG / RIGHT columns.
        assert "WRONG interpretation" in body, (
            "Priority Stacking section must include a worked-examples table with a "
            "'WRONG interpretation' column so the anti-pattern is concretely named."
        )
        assert "RIGHT interpretation" in body, (
            "Priority Stacking worked-examples table must have a 'RIGHT interpretation' "
            "column showing the additive interpretation."
        )

    @pytest.mark.parametrize(
        "directive",
        [
            "guardrails NOW",
            "don't wait on CI",
            "do X immediately",
        ],
    )
    def test_directive_in_examples(
        self, agents_md_content: str, directive: str
    ) -> None:
        body = _section(agents_md_content, "CRITICAL: Priority Stacking (AND not OR)")
        assert directive in body, (
            f"Priority Stacking worked-examples table must include the '{directive}' "
            f"directive — it was the concrete incident that motivated the rule."
        )


class TestAntiPatternEnumerated:
    """The forbidden anti-patterns must be enumerated, not implied."""

    @pytest.mark.parametrize(
        "fragment",
        [
            "pause everything else",
            "previous priorities are void",
            "blocking all subagent dispatch",
        ],
    )
    def test_forbidden_pattern_listed(
        self, agents_md_content: str, fragment: str
    ) -> None:
        body = _section(agents_md_content, "CRITICAL: Priority Stacking (AND not OR)")
        assert fragment in body, (
            f"Priority Stacking section must enumerate '{fragment}' as a forbidden "
            f"anti-pattern — enumerating the specific failure modes is what makes the "
            f"rule enforceable by the agent's own self-check."
        )


class TestEnforcementReference:
    """The section must reference its own enforcement mechanism (this test)."""

    def test_references_test_file(self, agents_md_content: str) -> None:
        body = _section(agents_md_content, "CRITICAL: Priority Stacking (AND not OR)")
        assert "test_priority_stacking_rule.py" in body, (
            "Priority Stacking section must reference its structural-pin test "
            "(tests/unit/test_priority_stacking_rule.py) — 3-layer guardrail "
            "discipline requires the prompt layer to name the test layer."
        )
