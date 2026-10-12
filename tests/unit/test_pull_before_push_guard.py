"""Exercise the actual push fetch guard against isolated, local Git remotes."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make") or "make"


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "PUSH_REMOTE", "FORCE",
        "GIT_DIR", "GIT_WORK_TREE",
    ):
        environment.pop(name, None)
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return environment


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=directory, env=_environment(),
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()


def _commit(directory: Path, message: str) -> None:
    _git(directory, "config", "user.name", "Guard fixture")
    _git(directory, "config", "user.email", "guard@example.invalid")
    _git(directory, "add", "note.txt")
    _git(directory, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", message)


@pytest.fixture
def repositories(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "--quiet", "--initial-branch=development")
    local = tmp_path / "local"
    local.mkdir()
    _git(local, "init", "--quiet", "--initial-branch=development")
    (local / "note.txt").write_text("initial\n", encoding="utf-8")
    _commit(local, "initial")
    _git(local, "remote", "add", "sandboxcom", str(remote))
    _git(local, "push", "--quiet", "sandboxcom", "development")
    shutil.copy(ROOT / "Makefile", local / "Makefile")
    shutil.copytree(ROOT / "make", local / "make")
    return local, remote


def _guard(local: Path, *variables: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [MAKE, "--no-print-directory", *variables, "_pull-before-push-guard"],
        cwd=local, env=_environment(), capture_output=True, text=True, check=False, timeout=30,
    )


def _advance_remote(local: Path, remote: Path) -> str:
    writer = local.parent / "writer"
    _git(local.parent, "clone", "--quiet", str(remote), str(writer))
    (writer / "note.txt").write_text("remote advance\n", encoding="utf-8")
    _commit(writer, "remote advance")
    _git(writer, "push", "--quiet", "origin", "development")
    return _git(writer, "rev-parse", "HEAD")


@pytest.mark.parametrize("variables", [(), ("PUSH_REMOTE=sandboxcom",)])
def test_default_and_explicit_remote_refresh_current_branch_without_publication(
    repositories: tuple[Path, Path], variables: tuple[str, ...],
) -> None:
    local, remote = repositories
    remote_before = _git(remote, "rev-parse", "refs/heads/development")
    (local / "note.txt").write_text("local advance\n", encoding="utf-8")
    _commit(local, "local advance")
    local_before = _git(local, "rev-parse", "HEAD")

    result = _guard(local, *variables)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"remote={remote_before} ahead=1 behind=0" in result.stdout
    assert "_pull-before-push-guard: PASS" in result.stdout
    assert _git(local, "rev-parse", "HEAD") == local_before
    assert _git(remote, "rev-parse", "refs/heads/development") == remote_before
    assert not (local / ".venv").exists()


@pytest.mark.parametrize("force", ["0", "1"])
def test_remote_advance_blocks_even_with_force_flag_and_keeps_local_head(
    repositories: tuple[Path, Path], force: str,
) -> None:
    local, remote = repositories
    local_before = _git(local, "rev-parse", "HEAD")
    remote_head = _advance_remote(local, remote)

    result = _guard(local, "PUSH_REMOTE=sandboxcom", f"FORCE={force}")

    assert result.returncode != 0
    assert "remote is 1 commit(s) ahead" in result.stdout
    assert "merge-forward" in result.stdout
    assert "_pull-before-push-guard: PASS" not in result.stdout
    assert _git(local, "rev-parse", "refs/remotes/sandboxcom/development") == remote_head
    assert _git(local, "rev-parse", "HEAD") == local_before


def test_divergence_is_not_mistaken_for_local_only_work(repositories: tuple[Path, Path]) -> None:
    local, remote = repositories
    _advance_remote(local, remote)
    (local / "note.txt").write_text("different local advance\n", encoding="utf-8")
    _commit(local, "local divergence")
    result = _guard(local, "PUSH_REMOTE=sandboxcom")
    assert result.returncode != 0
    assert "ahead=1 behind=1" in result.stdout
    assert "_pull-before-push-guard: PASS" not in result.stdout


def test_remote_lookup_failure_cannot_accept_stale_tracking_evidence(repositories: tuple[Path, Path]) -> None:
    local, remote = repositories
    cached = _git(local, "rev-parse", "refs/remotes/sandboxcom/development")
    remote.rename(remote.with_name("unavailable.git"))
    result = _guard(local, "PUSH_REMOTE=sandboxcom")
    assert result.returncode != 0
    assert "remote lookup failed" in result.stdout
    assert "_pull-before-push-guard: PASS" not in result.stdout
    assert _git(local, "rev-parse", "refs/remotes/sandboxcom/development") == cached


def test_confirmed_new_remote_branch_can_be_created_without_guard_publication(
    repositories: tuple[Path, Path],
) -> None:
    local, remote = repositories
    _git(local, "checkout", "--quiet", "-b", "feature-v012")
    result = _guard(local, "PUSH_REMOTE=sandboxcom")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "remote confirms branch feature-v012 is absent" in result.stdout
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads") == "refs/heads/development"


def test_detached_head_is_rejected_before_fetch(repositories: tuple[Path, Path]) -> None:
    local, _ = repositories
    _git(local, "checkout", "--quiet", "--detach")
    result = _guard(local, "PUSH_REMOTE=sandboxcom")
    assert result.returncode != 0
    assert "detached HEAD" in result.stdout
    assert "_pull-before-push-guard: PASS" not in result.stdout
    assert not (local / ".git/FETCH_HEAD").exists()


def test_make_dry_run_never_fetches_or_updates_cached_tracking(repositories: tuple[Path, Path]) -> None:
    local, remote = repositories
    _advance_remote(local, remote)
    cached = _git(local, "rev-parse", "refs/remotes/sandboxcom/development")
    result = _guard(local, "-n", "PUSH_REMOTE=sandboxcom")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "git fetch --no-tags" in result.stdout
    assert _git(local, "rev-parse", "refs/remotes/sandboxcom/development") == cached
    assert not (local / ".git/FETCH_HEAD").exists()


def test_fetch_failure_after_successful_lookup_cannot_use_cached_refs(
    repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, _ = repositories
    cached = _git(local, "rev-parse", "refs/remotes/sandboxcom/development")
    actual_git = shutil.which("git")
    assert actual_git is not None
    commands = local.parent / "commands"
    commands.mkdir()
    wrapper = commands / "git"
    wrapper.write_text(
        '#!/bin/sh\nif [ "$1" = "fetch" ]; then exit 75; fi\n'
        + f'exec "{actual_git}" "$@"\n', encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    result = _guard(local, "PUSH_REMOTE=sandboxcom")
    assert result.returncode != 0
    assert "fetch failed" in result.stdout
    assert "_pull-before-push-guard: PASS" not in result.stdout
    assert _git(local, "rev-parse", "refs/remotes/sandboxcom/development") == cached
