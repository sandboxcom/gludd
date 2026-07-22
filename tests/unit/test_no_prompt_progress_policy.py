from __future__ import annotations

from pathlib import Path


def test_agents_codifies_no_prompt_progress_rule() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "NO-PROMPT PROGRESS DIRECTIVE" in text
    assert "do not call tools or paths that trigger approval prompts" in text
    assert "abandon that path immediately, use or add a make target" in text
    assert "keep working without asking" in text
    assert "permission to edit files must never be requested again" in text
    assert "Only ask when progress is impossible because required external facts are unavailable" in text

def test_no_prompt_policy_is_a_first_read_directive() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    directive = text.index("NO-PROMPT PROGRESS DIRECTIVE")
    mechanical = text.index("Mechanical Contract")

    assert directive < mechanical
