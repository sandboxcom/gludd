"""Behavioral coverage for patch-equivalent cherry-pick rejection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _patch_equivalent_fixture(tmp_path: Path) -> tuple[Path, str, str]:
    """Create a copied patch followed by a conflicting local edit.

    ``duplicate`` changes ``old`` to ``new`` on the source branch. The target
    branch records that exact patch under another commit ID, then changes
    ``new`` to ``local``. Replaying ``duplicate`` without a preflight therefore
    enters a cherry-pick conflict even though ``git cherry`` marks it ``-``.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Gludd Tests")
    _git(repo, "config", "user.email", "gludd-tests@example.invalid")

    state = repo / "state.txt"
    state.write_text("alpha\nvalue=old\nomega\n", encoding="utf-8")
    base = _commit(repo, "base")
    target_branch = _git(repo, "branch", "--show-current").stdout.strip()

    _git(repo, "switch", "-c", "source")
    (repo / "unique.txt").write_text("unique source change\n", encoding="utf-8")
    unique = _commit(repo, "unique source patch")
    state.write_text("alpha\nvalue=new\nomega\n", encoding="utf-8")
    duplicate = _commit(repo, "source patch")

    _git(repo, "switch", target_branch)
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base
    state.write_text("alpha\nvalue=new\nomega\n", encoding="utf-8")
    copied = _commit(repo, "independently applied patch")
    assert copied != duplicate
    state.write_text("alpha\nvalue=local\nomega\n", encoding="utf-8")
    _commit(repo, "later local edit")

    cherry = _git(repo, "cherry", "HEAD", duplicate).stdout.splitlines()
    assert f"- {duplicate}" in cherry
    return repo, unique, duplicate


def _make(
    repo: Path,
    target: str,
    assignment: str,
    *,
    validate_only: str = "0",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-f",
            str(MAKEFILE),
            target,
            assignment,
            f"CHERRY_PICK_VALIDATE_ONLY={validate_only}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _assert_pristine(repo: Path, head: str) -> None:
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert _git(repo, "rev-parse", "--verify", "CHERRY_PICK_HEAD", check=False).returncode != 0


def test_single_target_blocks_patch_equivalent_commit_before_conflict(
    tmp_path: Path,
) -> None:
    repo, _unique, duplicate = _patch_equivalent_fixture(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _make(repo, "git-cherry-pick", f"SHA={duplicate}")

    assert result.returncode != 0
    assert "PATCH_EQUIVALENT_CHERRY_PICK_BLOCKED" in result.stdout + result.stderr
    _assert_pristine(repo, head)


def test_list_target_preflights_every_patch_before_first_mutation(
    tmp_path: Path,
) -> None:
    repo, unique, duplicate = _patch_equivalent_fixture(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _make(repo, "git-cherry-pick-list", f"SHAS={unique} {duplicate}")

    assert result.returncode != 0
    assert "PATCH_EQUIVALENT_CHERRY_PICK_BLOCKED" in result.stdout + result.stderr
    assert not (repo / "unique.txt").exists()
    _assert_pristine(repo, head)


@pytest.mark.parametrize(
    ("target", "variable"),
    (("git-cherry-pick", "SHA"), ("git-cherry-pick-list", "SHAS")),
)
def test_targets_still_apply_a_unique_patch(
    tmp_path: Path,
    target: str,
    variable: str,
) -> None:
    repo, unique, _duplicate = _patch_equivalent_fixture(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _make(repo, target, f"{variable}={unique}")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != head
    assert (repo / "unique.txt").read_text(encoding="utf-8") == "unique source change\n"
    assert _git(repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize(
    ("target", "assignment"),
    (("git-cherry-pick", "SHA=HEAD"), ("git-cherry-pick-list", "SHAS=HEAD")),
)
def test_documented_validation_modes_are_non_mutating(
    tmp_path: Path,
    target: str,
    assignment: str,
) -> None:
    repo, _unique, _duplicate = _patch_equivalent_fixture(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _make(repo, target, assignment, validate_only="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHERRY_PICK_VALIDATED" in result.stdout
    _assert_pristine(repo, head)
