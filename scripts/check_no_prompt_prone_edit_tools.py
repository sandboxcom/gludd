#!/usr/bin/env python3
"""Fail if prompt-prone edit tool policy is not executable and gate-enforced."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_AGENT_PHRASES = [
    "NO-PROMPT PROGRESS DIRECTIVE",
    "Do not use prompt-prone edit tools such as apply_patch.",
    "The Codex apply_patch tool is banned for this repo",
    "all file edits must use make targets",
    "harden the make target instead of asking",
]


def _fail(message: str) -> int:
    print(f"NO-PROMPT EDIT POLICY FAILED: {message}", file=sys.stderr)
    return 1


def main() -> int:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    missing = [phrase for phrase in REQUIRED_AGENT_PHRASES if phrase not in agents]
    if missing:
        return _fail("AGENTS.md missing required phrase(s): " + ", ".join(missing))

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    if "check-no-prompt-prone-edit-tools:" not in makefile:
        return _fail("Makefile target check-no-prompt-prone-edit-tools is missing")
    if "scripts/check_no_prompt_prone_edit_tools.py" not in makefile:
        return _fail("Makefile target does not run scripts/check_no_prompt_prone_edit_tools.py")

    gate_match = re.search(r"^gate: (?P<deps>.*)$", makefile, re.MULTILINE)
    if gate_match is None:
        return _fail("gate target line is missing")
    if "check-no-prompt-prone-edit-tools" not in gate_match.group("deps"):
        return _fail("gate does not include check-no-prompt-prone-edit-tools")

    help_block = makefile.split("help:", 1)[1].split("# ---", 1)[0]
    if "  check-no-prompt-prone-edit-tools" not in help_block:
        return _fail("make help does not document check-no-prompt-prone-edit-tools")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
