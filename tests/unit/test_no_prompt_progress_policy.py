from __future__ import annotations

from pathlib import Path


def test_agents_codifies_no_prompt_progress_rule() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "NO-PROMPT PROGRESS DIRECTIVE" in text
    assert "do not call tools or paths that trigger approval prompts" in text
    assert "abandon that path immediately, use or add a make target" in text
    assert "keep working without asking" in text
