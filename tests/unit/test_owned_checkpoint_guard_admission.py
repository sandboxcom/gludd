"""Native retained-checkpoint admission without dropping recoverable work."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "scripts" / "owned_git_checkpoint.py"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True,
    ).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "native-checkpoint"
    root.mkdir()
    _git(root, "init", "-b", "development")
    _git(root, "config", "user.name", "Checkpoint guard regression")
    _git(root, "config", "user.email", "checkpoint-guard@example.invalid")
    (root / "owned.txt").write_text("base\n")
    (root / "outside.txt").write_text("base\n")
    _git(root, "add", "owned.txt", "outside.txt")
    _git(root, "commit", "-m", "fixture")
    return root


def _checkpoint(root: Path, *, owner: str | None = None, files: str = "owned.txt") -> str:
    (root / "owned.txt").write_text("recoverable work\n")
    identity = owner or hashlib.sha256(os.fsencode(root.resolve())).hexdigest()
    message = f"gludd-owned-checkpoint label=guard-regression-001 owner={identity} files={files}"
    _git(root, "stash", "push", "--include-untracked", "--message", message, "--", "owned.txt")
    return _git(root, "rev-parse", "refs/stash").strip()


def _count(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(OWNER), "count-leaks"], cwd=root,
        text=True, capture_output=True, check=False,
    )


def test_owned_retained_checkpoint_is_not_a_hook_leak(repository: Path) -> None:
    sha = _checkpoint(repository)
    before = _git(repository, "stash", "list", "--format=%H:%s")
    result = _count(repository)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"
    assert sha in result.stderr
    assert _git(repository, "stash", "list", "--format=%H:%s") == before


def test_an_unknown_hook_stash_remains_blocking(repository: Path) -> None:
    (repository / "owned.txt").write_text("unknown hook work\n")
    _git(repository, "stash", "push", "--message", "pre-commit hook temporary work")
    before = _git(repository, "stash", "list", "--format=%H:%s")
    result = _count(repository)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    assert _git(repository, "stash", "list", "--format=%H:%s") == before


@pytest.mark.parametrize("forgery", ("owner", "scope"))
def test_owned_prefix_does_not_admit_foreign_identity_or_scope(repository: Path, forgery: str) -> None:
    _checkpoint(
        repository,
        owner="0" * 64 if forgery == "owner" else None,
        files="outside.txt" if forgery == "scope" else "owned.txt",
    )
    result = _count(repository)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_audit_does_not_mislabel_known_checkpoint_as_hook_conflict(repository: Path) -> None:
    sha = _checkpoint(repository)
    (repository / "outside.txt").write_text("unrelated current work\n")
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(ROOT / "make" / "50-development-and-guardrails.mk"),
         "_pre-commit-stash-audit", f"PYTHON={sys.executable}"],
        cwd=repository, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "git-stash-pop" not in result.stdout
    assert "auto-fixed files that are in stash" not in result.stdout
    assert _git(repository, "rev-parse", "refs/stash").strip() == sha
