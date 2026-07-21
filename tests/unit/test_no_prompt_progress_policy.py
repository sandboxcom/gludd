from __future__ import annotations

from pathlib import Path


def test_agents_codifies_no_prompt_progress_rule() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "NO-PROMPT PROGRESS DIRECTIVE" in text
    assert "do not call tools or paths that trigger approval prompts" in text
    assert "abandon that path immediately, use or add a make target" in text
    assert "keep working without asking" in text


def test_no_prompt_policy_is_a_first_read_directive() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    directive = text.index("NO-PROMPT PROGRESS DIRECTIVE")
    mechanical = text.index("Mechanical Contract")

    assert directive < mechanical

def test_status_answers_do_not_stop_active_work() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "Status questions are not completion checkpoints" in text
    assert "answer status only as an interim progress update" in text
    assert "continue with the next make-backed tool call in the same turn" in text
    assert "Never use a final response as a pause while known work remains" in text
