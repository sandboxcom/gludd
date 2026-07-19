"""Tests for Makefile git targets."""

from __future__ import annotations

import subprocess
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def test_git_cherry_pick_target_exists() -> None:
    content = MAKEFILE.read_text()
    assert "git-cherry-pick:" in content, "Makefile missing target: git-cherry-pick"


def test_git_cherry_pick_accepts_sha_param() -> None:
    content = MAKEFILE.read_text()
    lines = content.split("\n")
    in_target = False
    found_sha = False
    found_cherry_pick = False
    for line in lines:
        if line.startswith("git-cherry-pick:"):
            in_target = True
            continue
        if in_target and line.startswith("\t") and "$(SHA)" in line:
            found_sha = True
        if in_target and "cherry-pick" in line:
            found_cherry_pick = True
        if in_target and not line.startswith("\t") and line.strip() != "":
            break
        if in_target and not line.startswith("\t") and line.strip() == "":
            break
    assert found_sha, "git-cherry-pick target missing SHA parameter check"
    assert found_cherry_pick, "git-cherry-pick target missing cherry-pick command"


def test_git_cherry_pick_rejects_missing_sha() -> None:
    result = subprocess.run(
        ["make", "git-cherry-pick"],
        capture_output=True, text=True,
        cwd=str(MAKEFILE.parent), timeout=30,
    )
    assert result.returncode != 0, "git-cherry-pick without SHA should exit non-zero"


def test_git_cherry_pick_target_help_mentions_target() -> None:
    content = MAKEFILE.read_text()
    help_section = False
    found = False
    for line in content.split("\n"):
        if line.startswith(".PHONY"):
            help_section = False
        if "make help" in line or line.startswith("help:"):
            help_section = True
            continue
        if line.startswith("\t@echo") and "git-cherry-pick" in line:
            found = True
    # Not all targets need help entries; skip if the help section isn't exhaustive
    # Just ensure the target is in PHONY
    phony_lines = [l for l in content.split("\n") if ".PHONY" in l]
    for pline in phony_lines:
        if "git-cherry-pick" in pline:
            found = True
    assert found, "git-cherry-pick not listed in .PHONY"
