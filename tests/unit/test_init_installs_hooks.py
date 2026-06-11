"""V1.4: Verify make init installs pre-commit hooks."""
from __future__ import annotations

import re
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent.parent


def _makefile() -> str:
    return (_PROJECT / "Makefile").read_text(encoding="utf-8")


class TestMakeInitInstallsHooks:
    def test_init_target_runs_install_hooks(self):
        content = _makefile()
        assert "install-hooks" in content, "install-hooks target missing"
        block = re.search(r"^init:.*?(?=^\S+?:)", content, re.MULTILINE | re.DOTALL)
        assert block is not None, "init target not found"
        assert "install-hooks" in block.group(0), (
            "init target does not call install-hooks"
        )

    def test_install_hooks_target_exists(self):
        content = _makefile()
        assert "pre-commit install" in content, "pre-commit install not found"
        assert "install-hooks:" in content, "install-hooks target not found"
