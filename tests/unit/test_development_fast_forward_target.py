"""Execute the real reviewed-candidate fast-forward recipe against temporary Git."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.check_make_target_contract import _stanzas
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    """Run bounded Git commands confined to the test's own repository."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, timeout=10,
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, str, str]:
    """Create a genuine development ancestor and separate reviewed source."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "development")
    _git(repo, "config", "user.name", "Gludd fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "feature.txt").write_text("before\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "base")
    before = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "reviewed-source")
    (repo / "feature.txt").write_text("reviewed\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-a", "-m", "reviewed source")
    after = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "development")
    return repo, before, after


def _run(
    repository: tuple[Path, str, str], *, apply: str | None = "1", before: str | None = None,
    after: str | None = None, source: str = "reviewed-source", guard: str = "",
) -> subprocess.CompletedProcess[str]:
    """Replace only existing external guards, not the real recipe or Git."""
    repo, before_sha, after_sha = repository
    stanzas = _stanzas(compose_makefile(ROOT / "Makefile"))
    block = stanzas.get("development-fast-forward")
    assert block is not None, "the reviewed-candidate fast-forward target is missing"
    fixture = repo.parent / "fixture.mk"
    fixture.write_text(
        "APPLY ?= 0\n.PHONY: _gate-mutation-guard main-worktree-guard\n"
        "_gate-mutation-guard:\n\t@" + ("exit 27" if guard == "gate" else ":") + "\n"
        "main-worktree-guard:\n\t@" + ("exit 27" if guard == "main" else ":") + "\n"
        + block + "\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "GIT_DIR", "GIT_WORK_TREE"}}
    apply_argument = [] if apply is None else [f"APPLY={apply}"]
    return subprocess.run(
        [shutil.which("make") or "make", "--no-print-directory", "-f", str(fixture),
         "development-fast-forward", *apply_argument, f"SOURCE={source}",
         f"EXPECTED_BEFORE_SHA={before_sha if before is None else before}",
         f"EXPECTED_AFTER_SHA={after_sha if after is None else after}"],
        cwd=repo, env=env, check=False, capture_output=True, text=True, timeout=10,
    )


def _refs(repo: Path) -> str:
    """Snapshot all real refs to prove rejected admission made no ref writes."""
    return _git(repo, "show-ref", "--head")


def test_read_only_validation_keeps_every_ref(repository: tuple[Path, str, str]) -> None:
    repo, before, after = repository
    refs = _refs(repo)
    result = _run(repository, apply="0", before="HEAD", after="reviewed-source")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEVELOPMENT-FAST-FORWARD-VALIDATED" in result.stdout
    assert before in result.stdout and after in result.stdout
    assert _refs(repo) == refs
    assert (repo / "feature.txt").read_text() == "before\n"


def test_apply_fast_forwards_exact_candidate_without_extra_merge(
    repository: tuple[Path, str, str],
) -> None:
    repo, before, after = repository
    result = _run(repository)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEVELOPMENT-FAST-FORWARDED" in result.stdout
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "development"
    assert _git(repo, "rev-parse", "HEAD", "development", "reviewed-source").splitlines() == [after] * 3
    assert _git(repo, "rev-list", "--parents", "-n", "1", after).split() == [after, before]
    assert _git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert _git(repo, "tag", "--list") == ""
    assert _git(repo, "remote") == ""


def test_default_is_nonmutating_validation(repository: tuple[Path, str, str]) -> None:
    repo = repository[0]
    refs = _refs(repo)
    result = _run(repository, apply=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEVELOPMENT-FAST-FORWARD-VALIDATED" in result.stdout
    assert _refs(repo) == refs


@pytest.mark.parametrize("apply", ["0", "1"])
@pytest.mark.parametrize("field", ["before", "after"])
def test_valid_but_wrong_commit_is_rejected(
    repository: tuple[Path, str, str], apply: str, field: str,
) -> None:
    repo, before, after = repository
    refs = _refs(repo)
    wrong_sha = after if field == "before" else before
    result = _run(repository, apply=apply, **{field: wrong_sha})
    assert result.returncode != 0
    assert _refs(repo) == refs


@pytest.mark.parametrize("apply", ["", "2", "unknown"])
def test_invalid_apply_writes_no_refs(
    repository: tuple[Path, str, str], apply: str,
) -> None:
    repo = repository[0]
    refs = _refs(repo)
    result = _run(repository, apply=apply)
    assert result.returncode != 0
    assert _refs(repo) == refs
    assert "DEVELOPMENT-FAST-FORWARDED" not in result.stdout


@pytest.mark.parametrize("field", ["before", "after"])
@pytest.mark.parametrize("value", ["", "HEAD", "abc1234", "f" * 40])
def test_apply_rejects_missing_unpinned_or_mismatched_identity(
    repository: tuple[Path, str, str], field: str, value: str,
) -> None:
    repo = repository[0]
    refs = _refs(repo)
    result = _run(repository, **{field: value})
    assert result.returncode != 0
    assert _refs(repo) == refs
    assert "DEVELOPMENT-FAST-FORWARDED" not in result.stdout


@pytest.mark.parametrize("source", ["", "missing-source", "--help"])
def test_missing_or_option_like_source_writes_no_refs(
    repository: tuple[Path, str, str], source: str,
) -> None:
    repo = repository[0]
    refs = _refs(repo)
    result = _run(repository, source=source)
    assert result.returncode != 0
    assert _refs(repo) == refs


@pytest.mark.parametrize("dirty", ["tracked", "staged", "untracked"])
def test_dirty_development_writes_no_refs(
    repository: tuple[Path, str, str], dirty: str,
) -> None:
    repo = repository[0]
    (repo / ("untracked.txt" if dirty == "untracked" else "feature.txt")).write_text("dirty\n")
    if dirty == "staged":
        _git(repo, "add", "feature.txt")
    refs = _refs(repo)
    result = _run(repository)
    assert result.returncode != 0
    assert _refs(repo) == refs


def test_wrong_checkout_branch_writes_no_refs(repository: tuple[Path, str, str]) -> None:
    repo, _, after = repository
    _git(repo, "checkout", "-q", "reviewed-source")
    refs = _refs(repo)
    result = _run(repository, before=after)
    assert result.returncode != 0
    assert _refs(repo) == refs


def test_divergence_cannot_create_a_merge(repository: tuple[Path, str, str]) -> None:
    repo, _, after = repository
    (repo / "other.txt").write_text("divergent\n")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-q", "-m", "divergent development")
    before = _git(repo, "rev-parse", "HEAD")
    refs = _refs(repo)
    result = _run((repo, before, after))
    assert result.returncode != 0
    assert _refs(repo) == refs


@pytest.mark.parametrize("guard", ["gate", "main"])
def test_failed_ownership_guard_prevents_ref_writes(
    repository: tuple[Path, str, str], guard: str,
) -> None:
    repo = repository[0]
    refs = _refs(repo)
    result = _run(repository, guard=guard)
    assert result.returncode != 0
    assert _refs(repo) == refs


@pytest.mark.parametrize("hook", ["git update-ref refs/heads/development {before}",
                                   "git checkout -q reviewed-source"])
def test_post_merge_identity_drift_cannot_report_success(
    repository: tuple[Path, str, str], hook: str,
) -> None:
    repo, before, _ = repository
    path = repo / ".git" / "hooks" / "post-merge"
    path.write_text("#!/bin/sh\n" + hook.format(before=before) + "\n", encoding="utf-8")
    path.chmod(0o700)
    result = _run(repository)
    assert result.returncode != 0
    assert "DEVELOPMENT-FAST-FORWARDED" not in result.stdout
