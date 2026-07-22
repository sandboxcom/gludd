from __future__ import annotations

import subprocess
from pathlib import Path


def test_agents_codifies_no_prompt_progress_rule() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "NO-PROMPT PROGRESS DIRECTIVE" in text
    assert "do not call tools or paths that trigger approval prompts" in text
    assert "abandon that path immediately, use or add a make target" in text
    assert "keep working without asking" in text
    assert "Do not use prompt-prone edit tools such as apply_patch" in text
    assert "harden the make target instead of asking" in text


def test_no_prompt_policy_is_a_first_read_directive() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    directive = text.index("NO-PROMPT PROGRESS DIRECTIVE")
    mechanical = text.index("Mechanical Contract")

    assert directive < mechanical


def test_no_prompt_policy_is_gate_enforced() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "check-no-prompt-prone-edit-tools:" in makefile
    assert "scripts/check_no_prompt_prone_edit_tools.py" in makefile
    gate_line = next(line for line in makefile.splitlines() if line.startswith("gate:"))
    assert "check-no-prompt-prone-edit-tools" in gate_line


def test_no_prompt_policy_bans_apply_patch_unconditionally() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "The Codex apply_patch tool is banned for this repo" in text
    assert "all file edits must use make targets" in text
    assert "when a make edit target can do the work" not in text


def test_no_prompt_checker_target_executes_successfully() -> None:
    result = subprocess.run(
        ["make", "check-no-prompt-prone-edit-tools"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
