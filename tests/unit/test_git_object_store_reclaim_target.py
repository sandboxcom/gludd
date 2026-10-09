"""Behavioral contract for bounded, branch-neutral Git object reclamation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKE_FRAGMENT = ROOT / "make" / "20-recovery-and-git.mk"
FOUNDATION = ROOT / "make" / "00-foundation.mk"
CONTRACT = ROOT / "config" / "make_target_contract.json"
DOC = ROOT / "docs" / "features" / "GIT_OBJECT_STORE_RECLAMATION.md"


def _git(
    repo: Path,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=development")
    tracked = repo / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(
        repo,
        "-c",
        "user.name=Gludd Test",
        "-c",
        "user.email=gludd-test@example.invalid",
        "commit",
        "-m",
        "initial",
    )
    return repo


def _old_unreachable_blob(repo: Path) -> tuple[str, Path]:
    oid = _git(repo, "hash-object", "-w", "--stdin", input_text="orphan\n").stdout.strip()
    object_path = repo / ".git" / "objects" / oid[:2] / oid[2:]
    old = time.time() - (40 * 24 * 60 * 60)
    os.utime(object_path, (old, old))
    return oid, object_path


def _run_target(
    repo: Path,
    *,
    validate_only: int,
    confirm: str = "",
    prune_days: int = 30,
    timeout_secs: int = 30,
    kill_after_secs: int = 3,
    heartbeat_secs: int = 1,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "git-object-store-reclaim",
            f"GIT_OBJECT_STORE_REPOSITORY={repo}",
            f"GIT_OBJECT_STORE_CONFIRM={confirm}",
            f"GIT_OBJECT_STORE_PRUNE_DAYS={prune_days}",
            f"GIT_OBJECT_STORE_TIMEOUT_SECS={timeout_secs}",
            f"GIT_OBJECT_STORE_KILL_AFTER_SECS={kill_after_secs}",
            f"GIT_OBJECT_STORE_HEARTBEAT_SECS={heartbeat_secs}",
            f"GIT_OBJECT_STORE_VALIDATE_ONLY={validate_only}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
        env=env,
    )


def _logical_state(repo: Path) -> dict[str, str]:
    return {
        "head": _git(repo, "rev-parse", "HEAD").stdout,
        "refs": _git(repo, "show-ref", "--head").stdout,
        "status": _git(
            repo,
            "status",
            "--porcelain=v2",
            "--branch",
            "--untracked-files=all",
        ).stdout,
        "worktrees": _git(repo, "worktree", "list", "--porcelain").stdout,
    }


def test_target_contract_is_registered_with_every_safety_control() -> None:
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))["targets"]
    target = next(item for item in contracts if item["name"] == "git-object-store-reclaim")

    assert target["make_variables"] == [
        "GIT_OBJECT_STORE_REPOSITORY",
        "GIT_OBJECT_STORE_CONFIRM",
        "GIT_OBJECT_STORE_PRUNE_DAYS",
        "GIT_OBJECT_STORE_TIMEOUT_SECS",
        "GIT_OBJECT_STORE_KILL_AFTER_SECS",
        "GIT_OBJECT_STORE_HEARTBEAT_SECS",
        "GIT_OBJECT_STORE_VALIDATE_ONLY",
    ]
    for variable in target["make_variables"]:
        assert f"{variable}=" in target["behavior"]


def test_target_uses_only_conservative_git_maintenance_and_pins_invariants() -> None:
    make_text = MAKE_FRAGMENT.read_text(encoding="utf-8")
    target = make_text.split("git-object-store-reclaim:", maxsplit=1)[1]
    target = target.split("\n.PHONY:", maxsplit=1)[0]

    assert "git count-objects -vH" in target
    assert "gc.packRefs=false" in target
    assert "gc.reflogExpire=never" in target
    assert "gc.reflogExpireUnreachable=never" in target
    assert "gc.worktreePruneExpire=never" in target
    assert 'gc --no-detach --prune="$(GIT_OBJECT_STORE_PRUNE_DAYS).days.ago"' in target
    assert "git fsck --connectivity-only --no-dangling" in target
    assert "GIT_OBJECT_STORE_HEARTBEAT" in target
    assert "GIT_OBJECT_STORE_TIMEOUT" in target
    assert "refs_before" in target and "refs_after" in target
    assert "status_before" in target and "status_after" in target
    assert "worktrees_before" in target and "worktrees_after" in target
    assert "--prune=now" not in target
    assert "--aggressive" not in target
    assert "gc --force" not in target


def test_validate_only_inventories_without_changing_an_old_orphan(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    oid, object_path = _old_unreachable_blob(repo)
    before = _logical_state(repo)

    result = _run_target(repo, validate_only=1)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "GIT_OBJECT_STORE_INVENTORY" in result.stdout
    assert "GIT_OBJECT_STORE_VALID" in result.stdout
    assert "count:" in result.stdout
    assert object_path.exists()
    assert _git(repo, "cat-file", "-e", oid, check=False).returncode == 0
    assert _logical_state(repo) == before


def test_apply_requires_exact_confirmation_and_minimum_grace(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _, object_path = _old_unreachable_blob(repo)

    unconfirmed = _run_target(repo, validate_only=0, confirm="wrong")
    too_young = _run_target(
        repo,
        validate_only=0,
        confirm="RECLAIM-GIT-OBJECTS",
        prune_days=29,
    )

    assert unconfirmed.returncode != 0
    assert "GIT_OBJECT_STORE_CONFIRM must equal RECLAIM-GIT-OBJECTS" in unconfirmed.stdout
    assert too_young.returncode != 0
    assert "GIT_OBJECT_STORE_PRUNE_DAYS must be an integer of at least 30" in too_young.stdout
    assert object_path.exists()


def test_apply_reclaims_old_orphan_and_preserves_refs_and_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    oid, _ = _old_unreachable_blob(repo)
    (repo / "tracked.txt").write_text("locally modified\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("operator data\n", encoding="utf-8")
    before = _logical_state(repo)

    result = _run_target(
        repo,
        validate_only=0,
        confirm="RECLAIM-GIT-OBJECTS",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "GIT_OBJECT_STORE_BEGIN" in result.stdout
    assert "GIT_OBJECT_STORE_POSTCHECK phase=invariants" in result.stdout
    assert "GIT_OBJECT_STORE_POSTCHECK phase=connectivity" in result.stdout
    assert "GIT_OBJECT_STORE_READY" in result.stdout
    assert _git(repo, "cat-file", "-e", oid, check=False).returncode != 0
    assert _logical_state(repo) == before
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "locally modified\n"
    assert (repo / "untracked.txt").read_text(encoding="utf-8") == "operator data\n"


def test_apply_is_branch_neutral_from_a_linked_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-b", "feature", str(linked))
    oid, _ = _old_unreachable_blob(repo)
    before = _logical_state(linked)

    result = _run_target(
        linked,
        validate_only=0,
        confirm="RECLAIM-GIT-OBJECTS",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(linked, "branch", "--show-current").stdout.strip() == "feature"
    assert _git(linked, "cat-file", "-e", oid, check=False).returncode != 0
    assert _logical_state(linked) == before


def test_hung_git_gc_is_bounded_and_postchecked(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/bin/sh
set -eu
case " $* " in
  *" gc --no-detach "*)
    trap 'exit 143' TERM
    while :; do :; done
    ;;
  *) exec "$REAL_GIT" "$@" ;;
esac
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    real_git = shutil.which("git")
    assert real_git is not None
    env = os.environ.copy()
    env.update(
        {
            "PATH": os.pathsep.join((str(fake_bin), env["PATH"])),
            "REAL_GIT": real_git,
        }
    )
    before = _logical_state(repo)

    result = _run_target(
        repo,
        validate_only=0,
        confirm="RECLAIM-GIT-OBJECTS",
        timeout_secs=1,
        kill_after_secs=2,
        heartbeat_secs=1,
        env=env,
    )

    assert result.returncode != 0
    assert "GIT_OBJECT_STORE_HEARTBEAT" in result.stdout
    assert "GIT_OBJECT_STORE_TIMEOUT" in result.stdout
    assert "GIT_OBJECT_STORE_POSTCHECK phase=invariants" in result.stdout
    assert "GIT_OBJECT_STORE_POSTCHECK phase=connectivity" in result.stdout
    assert "Git object-store reclamation failed or exceeded its bound: rc=124" in result.stdout
    assert _logical_state(repo) == before


def test_help_and_documentation_explain_zdd_rollback_and_practitioner_evidence() -> None:
    foundation = FOUNDATION.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert "git-object-store-reclaim" in foundation
    assert "## Zero-downtime operation and rollback" in doc
    assert "## Upstream and practitioner evidence" in doc
    assert "git-scm.com/docs/git-gc" in doc
    assert "github.com/openai/codex/issues/29388" in doc
    assert "30" in doc and "reflog" in doc
