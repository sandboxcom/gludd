"""Regression tests for the make-only repository grep helper."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_grep_target(scope_argument: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "grep", "Q=GAME_E2E", scope_argument],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_grep_target_uses_non_reserved_search_path_input() -> None:
    result = _run_grep_target("SEARCH_PATH=docs")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "docs/" in result.stdout
    assert "grep: command not found" not in result.stderr


def test_grep_target_safely_supports_legacy_path_argument() -> None:
    result = _run_grep_target("PATH=docs")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "docs/" in result.stdout
    assert "grep: command not found" not in result.stderr
