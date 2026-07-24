"""RP.17 — Root Cause Escalation (3-Strike Rule) structural tests.

Codifies the rule that after the third CI failure of the same class (timeout,
cancellation, dependency failure, YAML parse error), the agent MUST stop
patching symptoms and fix the systemic root cause instead.

This is a prompt-layer guardrail: the rule lives in AGENTS.md as proactive
instruction to every agent reading it. These tests verify the load-bearing
pieces of the section exist and would catch a regression that strips it.

See AGENTS.md "Root Cause Escalation (3-Strike Rule)" and the parent
"Root-Cause-Only Fix Policy" section.
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


class TestRootCauseEscalationSectionExists:
    """The section heading and key phrases must be present in AGENTS.md."""

    def test_section_heading_present(self, agents_src):
        assert "Root Cause Escalation" in agents_src, (
            "AGENTS.md must contain the 'Root Cause Escalation' section heading."
        )

    def test_stop_patching_directive_present(self, agents_src):
        assert "STOP patching" in agents_src, (
            "AGENTS.md must contain the 'STOP patching' directive — it is the "
            "behavioral trigger that tells the agent to stop applying symptom fixes."
        )

    def test_three_strike_phrase_present(self, agents_src):
        assert "3-Strike" in agents_src or "third time" in agents_src, (
            "AGENTS.md must reference either '3-Strike' or 'third time' so the "
            "rule's trigger threshold is unambiguous."
        )
