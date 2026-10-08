"""Regression tests for bounded cleanup of verified project processes."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    source = compose_makefile(MAKEFILE)
    match = re.search(
        rf"(?m)^{re.escape(target)}:\n((?:\t.*\n)+)",
        source,
    )
    assert match, f"missing Make target: {target}"
    return match.group(1)


def test_kill_project_pid_escalates_stubborn_verified_processes() -> None:
    """A verified stale process must not survive an ignored SIGTERM."""
    recipe = _recipe("kill-project-pid")
    assert "/bin/kill -TERM" in recipe
    assert "/bin/kill -0" in recipe
    assert "/bin/kill -KILL" in recipe
