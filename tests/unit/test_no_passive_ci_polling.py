"""CRITICAL: CI Status Must Be Actionable — No Passive Polling.

Verifies the "CI Status Must Be Actionable — No Passive Polling" section is
present in AGENTS.md as a prompt-layer guardrail. This section was codified
after the agent repeatedly reported only workflow-level ternary status
("in_progress" / "success" / "failure") instead of using rich targets to see
which shard had failed and taking action.

These tests pin the load-bearing section heading, the rich-target list, and the
actionable-next-step rules so a regression that strips them is caught at gate
time.

See AGENTS.md "CRITICAL: CI Status Must Be Actionable — No Passive Polling".
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
AGENTS_MD = ROOT / "AGENTS.md"

SECTION_HEADING = "CRITICAL: CI Status Must Be Actionable — No Passive Polling"

# Load-bearing phrases in the section body. Each encodes a rule; stripping any
# one weakens the guardrail.
KEY_PHRASES = [
    "workflow-level ternary state",
    "passive polling",
    "actionable insight",
    "do not poll again with `make ci-status`",
    "rich target",
    "reproduce a failing shard locally",
    "Made and pushed a code change",
]

# Rich CI insight targets the section must name as replacements for bare
# ci-status. These are the canonical make targets that surface per-job/step
# detail.
RICH_TARGETS = [
    "make ci-run-summary RUN=<id>",
    "make ci-view RUN=<id>",
    "make ci-dashboard",
    "make ci-active",
    "make ci-annotations-anon RUN=<id>",
    "make ci-checkrun-anno CHECK=<id>",
    "make ci-job-log RUN=<id> JOB=<substring>",
    "make ci-faillog RUN=<id>",
    "make ci-diagnose RUN=<id>",
]

# Anti-patterns the section must explicitly forbid.
EXPECTED_ANTI_PATTERNS = (
    "Build and Release is still in_progress",
    "Calling `make ci-status` three or more times in a row",
    "Waiting for a run to finish before investigating a job that already shows `failure`",
    "Reporting CI state without also stating what failed",
)


@pytest.fixture(scope="module")
def agents_src():
    if not AGENTS_MD.exists():
        pytest.fail("AGENTS.md must exist at the repo root.")
    return AGENTS_MD.read_text()


@pytest.fixture(scope="module")
def section_src(agents_src):
    """Extract the target section body and collapse whitespace."""
    import re

    idx = agents_src.find(SECTION_HEADING)
    if idx < 0:
        pytest.fail(f"AGENTS.md must contain the section heading '{SECTION_HEADING}'.")
    after = agents_src[idx + len(SECTION_HEADING) :]
    next_heading = after.find("\n## ")
    section = agents_src[idx:] if next_heading < 0 else agents_src[idx : idx + len(SECTION_HEADING) + next_heading]
    return re.sub(r"\s+", " ", section)


class TestSectionHeadingPresent:
    """The section heading must exist as a top-level CRITICAL section."""

    def test_heading_present(self, agents_src):
        assert SECTION_HEADING in agents_src, (
            f"AGENTS.md must contain the section heading '{SECTION_HEADING}' "
            "— the prompt-layer guardrail against passive CI polling."
        )

    def test_heading_is_top_level(self, agents_src):
        assert f"\n## {SECTION_HEADING}" in agents_src, (
            f"'{SECTION_HEADING}' must be a top-level (##) heading in AGENTS.md, not a subsection."
        )


class TestKeyPhrasesPresent:
    """Each load-bearing phrase must appear within the section body."""

    @pytest.mark.parametrize("phrase", KEY_PHRASES, ids=[p[:30] for p in KEY_PHRASES])
    def test_phrase_in_section(self, section_src, phrase):
        assert phrase in section_src, (
            f"AGENTS.md '{SECTION_HEADING}' section must contain the phrase: "
            f"{phrase!r}. Striking it weakens the no-passive-polling guardrail."
        )


class TestRichTargetsListed:
    """The section must list the canonical rich CI insight targets."""

    @pytest.mark.parametrize("target", RICH_TARGETS, ids=[t[:30] for t in RICH_TARGETS])
    def test_target_in_section(self, section_src, target):
        assert target in section_src, (
            f"AGENTS.md '{SECTION_HEADING}' section must list the rich target "
            f"{target!r} as a replacement for bare `ci-status`."
        )


class TestAntiPatternsPresent:
    """The anti-patterns list must call out the specific failure modes."""

    @pytest.mark.parametrize("phrase", EXPECTED_ANTI_PATTERNS, ids=[p[:30] for p in EXPECTED_ANTI_PATTERNS])
    def test_anti_pattern_listed(self, section_src, phrase):
        assert phrase in section_src, (
            f"AGENTS.md '{SECTION_HEADING}' section must list anti-pattern: "
            f"{phrase!r}. The anti-patterns list is what makes the rule "
            "actionable — without it, the agent can rationalize violations."
        )


class TestActionableNextSteps:
    """The section must require concrete next actions after CI observation."""

    def test_reproduce_shard_locally(self, section_src):
        assert "make test-ci-shard SHARD=<name>" in section_src, (
            "AGENTS.md must direct the agent to reproduce a failed shard locally "
            "with `make test-ci-shard SHARD=<name>`."
        )

    def test_fix_commit_push(self, section_src):
        assert "Fix the test or code, verify the shard passes, commit, and push" in section_src, (
            "AGENTS.md must require fix → verify → commit → push after a failed shard."
        )
