"""Structural pin: Release Candidate Discipline codification.

Verifies:
  - AGENTS.md contains the "CRITICAL: Release Candidate Discipline" section
  - Each of the five numbered rules is present with its load-bearing phrase
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = ROOT / "AGENTS.md"

SECTION_HEADING = "## CRITICAL: Release Candidate Discipline — Promote the Green Commit"

RULES: list[tuple[str, list[str]]] = [
    (
        "1. Green HEAD → promote immediately",
        [
            "make ci-verdict BRANCH=development",
            "test-ci-dual-track-local-bg",
            "make release-promote",
            "do NOT add new commits",
        ],
    ),
    (
        "2. Start local dual-track attestation right after pushing",
        [
            "test-ci-dual-track-local-bg",
            "parallel with hosted CI",
            "test-ci-dual-track-local-status",
            "natural breaks",
        ],
    ),
    (
        "3. Never re-run a full test suite on the same SHA",
        [
            "DUAL_TRACK_RESUME=1",
            "incremental resume",
            "same SHA",
        ],
    ),
    (
        "4. While CI is pending, only do release-advancing actions",
        [
            "README/status/tag hygiene",
            "side work",
            "status-only polling",
        ],
    ),
    (
        "5. No text-only responses or premature stops while a release is pending",
        [
            "No text-only responses",
            "premature stops",
            "release is pending",
        ],
    ),
]


@pytest.fixture(scope="module")
def agents_md_text() -> str:
    """Read AGENTS.md once for all assertions in this module."""
    assert AGENTS_MD.exists(), "AGENTS.md must exist at repo root"
    return AGENTS_MD.read_text()


def _section_body(content: str) -> str:
    """Return the body of the release-candidate-discipline section."""
    start = content.find(SECTION_HEADING)
    assert start != -1, (
        f"AGENTS.md missing '{SECTION_HEADING}' section. The release-candidate discipline codification must be present."
    )
    # Body extends to the next top-level `## ` heading.
    next_section = content.find("\n## ", start + len(SECTION_HEADING))
    if next_section == -1:
        return content[start:]
    return content[start:next_section]


def test_agents_md_has_release_candidate_discipline_section(
    agents_md_text: str,
) -> None:
    """AGENTS.md must contain the Release Candidate Discipline section."""
    assert SECTION_HEADING in agents_md_text, "AGENTS.md missing 'CRITICAL: Release Candidate Discipline' section."


def test_agents_md_section_contains_all_five_numbered_rules(
    agents_md_text: str,
) -> None:
    """The section must contain all five numbered rules."""
    body = _section_body(agents_md_text)
    for rule_number, _ in enumerate(RULES, start=1):
        marker = f"{rule_number}. **"
        assert marker in body, (
            f"AGENTS.md Release Candidate Discipline section missing rule {rule_number} "
            f"(expected numbered marker '{marker}')."
        )


@pytest.mark.parametrize(
    "rule_number, required_phrases",
    [(i + 1, phrases) for i, (_, phrases) in enumerate(RULES)],
)
def test_agents_md_rule_contains_load_bearing_phrases(
    agents_md_text: str,
    rule_number: int,
    required_phrases: list[str],
) -> None:
    """Each numbered rule must carry its load-bearing phrases."""
    body = _section_body(agents_md_text)
    # Extract just this rule's paragraph by finding its marker and the next rule's.
    start_marker = f"{rule_number}. **"
    start = body.find(start_marker)
    assert start != -1, f"Rule {rule_number} marker not found in section."
    next_rule = body.find(f"{rule_number + 1}. **", start + len(start_marker))
    rule_body = body[start:next_rule] if next_rule != -1 else body[start:]

    for phrase in required_phrases:
        assert phrase.lower() in rule_body.lower(), (
            f"Release Candidate Discipline rule {rule_number} must contain "
            f"the phrase '{phrase}'. Removing it weakens the rule."
        )
