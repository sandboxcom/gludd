"""Contract tests for the transactional development merge-forward target."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run_target(*variables: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "development-merge-forward", *variables],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _recipe() -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(
        r"^development-merge-forward:\n(?P<recipe>(?:\t.*\n)+)",
        makefile,
        re.MULTILINE,
    )
    assert match, "development-merge-forward target is missing"
    return match.group("recipe")


def test_content_mode_dry_run_is_default_and_non_mutating() -> None:
    result = _run_target("SOURCE=HEAD", "MODE=content")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "MERGE_FORWARD_DRY_RUN" in result.stdout
    assert "source=HEAD mode=content apply=0" in result.stdout
    assert "no repository changes were made" in result.stdout


def test_ancestry_only_dry_run_is_explicitly_auditable() -> None:
    result = _run_target("SOURCE=HEAD", "MODE=ancestry-only", "APPLY=0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING" in result.stdout
    assert "strategy=ours" in result.stdout
    assert "mode=ancestry-only" in result.stdout


def test_target_has_transactional_apply_guards() -> None:
    recipe = _recipe()

    for fragment in (
        "rev-parse --verify",
        'CURRENT_BRANCH="$$(git branch --show-current)"',
        '"$$CURRENT_BRANCH" != "development"',
        "status --porcelain",
        "merge --no-ff --no-commit",
        "-X ours",
        "diff --name-only --diff-filter=U",
        "merge --abort",
        "collect-check",
        "merge --no-ff -s ours",
    ):
        assert fragment in recipe


def test_ancestry_only_forbids_master_source() -> None:
    result = _run_target("SOURCE=master", "MODE=ancestry-only", "APPLY=0")

    assert result.returncode != 0
    assert "ancestry-only mode is forbidden for master" in result.stdout


def test_contract_doc_cites_long_lived_cherry_pick_reports() -> None:
    guidance = (ROOT / "docs/MAKE_TARGET_CONTRACT.md").read_text(encoding="utf-8")

    assert "stackoverflow.com/questions/45690696" in guidance
    assert "stackoverflow.com/questions/3757075" in guidance
    assert "development-merge-forward" in guidance
