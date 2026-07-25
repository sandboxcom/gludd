"""BP.8: Verify pre-commit lint hook exists, is executable, and runs make lint."""
from __future__ import annotations

import os
import re
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent.parent
_HOOK = _PROJECT / "scripts" / "hooks" / "pre-commit-lint"


def _makefile() -> str:
    return (_PROJECT / "Makefile").read_text(encoding="utf-8")


class TestPreCommitLintHook:
    def test_hook_script_exists(self):
        assert _HOOK.is_file(), f"pre-commit-lint hook not found at {_HOOK}"

    def test_hook_script_is_executable(self):
        if not _HOOK.is_file():
            return
        mode = _HOOK.stat().st_mode
        assert mode & os.X_OK, "pre-commit-lint is not executable"

    def test_install_hooks_target_references_hook(self):
        content = _makefile()
        block = re.search(
            r"^install-hooks:.*?(?=^\S+?:)", content, re.MULTILINE | re.DOTALL
        )
        assert block is not None, "install-hooks target not found in Makefile"
        assert "pre-commit-lint" in block.group(0), (
            "install-hooks target does not reference scripts/hooks/pre-commit-lint"
        )
        assert ".git/hooks/pre-commit" in block.group(0), (
            "install-hooks target does not install to .git/hooks/pre-commit"
        )

    def test_hook_script_runs_make_lint(self):
        content = _HOOK.read_text(encoding="utf-8")
        assert "make lint" in content, "hook script does not invoke make lint"
        assert "set -e" in content, "hook script does not set -e (fail-fast)"
