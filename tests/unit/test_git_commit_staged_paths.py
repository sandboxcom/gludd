"""Regression tests for lossless staged-path handling in ``make git-commit``."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_git_commit_does_not_store_nul_delimited_paths_in_a_shell_variable() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\ngit-commit:", 1)[1].split("\n\n", 1)[0]

    assert 'STAGED_FILES="$$(git diff --cached --name-only -z)"' not in recipe
    assert recipe.count("git diff --cached --name-only -z | xargs -0") == 2
    assert "pre-commit run --files" in recipe
    assert "xargs -0 git add" in recipe
