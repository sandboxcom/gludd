"""Shared structural verifier for AGENTS.md sections.

Many TASKS.md items across the OD (Operational Discipline), DC (Discipline
Codification), RP (Root Cause / Release Pipeline), and adjacent phases
reference `test_agents_md_section.py` as their `verify:` field. The items
individually assert that a specific AGENTS.md section exists and carries the
load-bearing phrases that distinguish the rule from a platitude. This shared
data-driven test pins them all in one place so a regression that strips or
renames a section fails loudly here instead of silently weakening policy.

The tests verify PRESENCE + key phrases only — they do not exercise runtime
behavior. The rules themselves are proactive prompt-layer instructions; the
mechanical enforcement lives in the `.opencode/plugin/*.ts` hooks.

Run:  make test-specific TESTFILE='tests/unit/test_agents_md_section'
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

ROOT = Path(__file__).parent.parent.parent
AGENTS_MD = ROOT / "AGENTS.md"

# (section_heading_substring, [required_phrases]). The heading substring is
# matched case-insensitively against `## CRITICAL: ...` headings; the phrases
# are matched case-insensitively against the section body. Each phrase is a
# load-bearing token — removing it should make the rule fail this test.
SECTIONS: list[tuple[str, list[str]]] = [
    ("Root Cause Escalation", ["3-strike", "systemic"]),
    ("Intermediate Progress Is Not Completion", ["verify-release-completeness"]),
    ("Follow Explicit Instructions Exactly", ["measurable requirement"]),
    ("CI Is Fire-and-Forget", ["natural breaks"]),
    ("No Text-Only Responses With Pending Work", ["tool call"]),
    ("Answer Direct Questions Directly", ["Yes", "No"]),
    ("Don't Rationalize Stops", ["malfunction"]),
    ("Don't Override User Instructions", ["NO exceptions"]),
    ("Don't Make Artifacts Optional", ["12/12"]),
    ("Don't Push Broken Code Without Lint", ["make lint"]),
    ("CI Wait Productivity", ["dispatch subagents"]),
    ("Polling CI Is Not Work", ["3 times"]),
    ("Git Operations Are Not Grinding", ["GIT_SHIPPING_TARGETS"]),
    ("Plugin Hook Invocation Validation", ["check-plugin-hook-invoke"]),
    ("Pipeline Completion Is The Primary Objective", ["release"]),
    ("No External File Access", ["NEVER"]),
]


@pytest.fixture(scope="module")
def agents_md_content() -> str:
    """Read AGENTS.md once for all assertions in this module."""
    assert AGENTS_MD.exists(), "AGENTS.md must exist at repo root"
    return AGENTS_MD.read_text()


def _find_section_heading(content: str, heading_substring: str) -> str:
    """Return the full markdown heading line matching the substring.

    A section is either:
      * a top-level policy block `## CRITICAL: <heading>` (e.g. DC.1, DC.3,
        Root Cause Escalation), OR
      * a sub-rule `### OD.N — <heading>` inside the umbrella Operational
        Discipline Rules section.

    Both forms are matched case-insensitively against `heading_substring`.
    Raises AssertionError with a helpful message if neither form is found.
    """
    needle = heading_substring.lower()
    for line in content.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith("## CRITICAL:")
            or stripped.startswith("### OD.")
        ) and needle in stripped.lower():
            return stripped
    raise AssertionError(
        f"AGENTS.md has no `## CRITICAL:` heading or `### OD.N` sub-rule "
        f"matching '{heading_substring}'. This section is referenced by "
        f"multiple TASKS.md items as their verify field; removing or renaming "
        f"it silently drops the policy."
    )


def _section_body(content: str, heading_substring: str) -> str:
    """Extract heading_line + body up to the next heading of the same depth.

    Returns the heading line AND its body so phrase checks can match tokens
    that appear in the heading itself (e.g. '3-Strike' in
    'Root Cause Escalation (3-Strike Rule)'). Body extends to the next
    heading of the same or shallower depth.
    """
    heading_line = _find_section_heading(content, heading_substring)
    # Determine depth: count leading '#' chars.
    hashes = len(heading_line) - len(heading_line.lstrip("#"))
    start = content.find(heading_line)
    next_section = content.find(f"\n{'#' * hashes} ", start + 1)
    # Also stop at any shallower heading (fewer '#' chars).
    if next_section == -1:
        return content[start:]
    return content[start:next_section]


def _flatten_cases() -> list[tuple[str, str]]:
    """Flatten SECTIONS into (heading, phrase) pairs for parametrize."""
    cases: list[tuple[str, str]] = []
    for heading, phrases in SECTIONS:
        for phrase in phrases:
            cases.append((heading, phrase))
    return cases


class TestSectionHeadingsPresent:
    """Every named section heading must exist in AGENTS.md."""

    @pytest.mark.parametrize(
        "heading_substring", [h for h, _ in SECTIONS]
    )
    def test_heading_present(
        self, agents_md_content: str, heading_substring: str
    ) -> None:
        # Should not raise — raises AssertionError with a useful message
        # naming the missing heading and why it matters.
        _find_section_heading(agents_md_content, heading_substring)


class TestSectionKeyPhrasesPresent:
    """Each section must carry its load-bearing phrases."""

    @pytest.mark.parametrize(
        "heading_substring, phrase", _flatten_cases()
    )
    def test_phrase_present(
        self, agents_md_content: str, heading_substring: str, phrase: str
    ) -> None:
        body = _section_body(agents_md_content, heading_substring)
        assert phrase.lower() in body.lower(), (
            f"Section '{heading_substring}' must contain the phrase "
            f"'{phrase}' (case-insensitive). This phrase is the load-bearing "
            f"token that distinguishes the rule from a platitude — without it "
            f"the section reads as a generic guideline and the failure mode "
            f"it was written to prevent regresses."
        )


class TestSectionListComplete:
    """Guard against accidental truncation of the SECTIONS list."""

    def test_at_least_16_sections_pinned(self) -> None:
        # The shared test exists to pin ~40 TASKS.md items at once. If the
        # SECTIONS list shrinks below 16, sections are being silently dropped
        # and the test no longer covers what TASKS.md references it for.
        assert len(SECTIONS) >= 16, (
            f"SECTIONS list must cover at least 16 AGENTS.md sections "
            f"(currently {len(SECTIONS)}). This test is the shared verify "
            f"target for many TASKS.md items; shrinking the list silently "
            f"unpins those items."
        )

    def test_every_section_has_at_least_one_phrase(self) -> None:
        for heading, phrases in SECTIONS:
            assert isinstance(phrases, list) and len(phrases) >= 1, (
                f"Section '{heading}' must declare at least one load-bearing "
                f"phrase — a heading-only check is too weak (a renamed "
                f"section with empty body would still pass)."
            )


def _all_phrases() -> Iterable[str]:
    for _, phrases in SECTIONS:
        yield from phrases


def test_no_duplicate_sections() -> None:
    headings = [h for h, _ in SECTIONS]
    assert len(headings) == len(set(headings)), (
        f"SECTIONS contains duplicate headings: "
        f"{[h for h in headings if headings.count(h) > 1]}"
    )
