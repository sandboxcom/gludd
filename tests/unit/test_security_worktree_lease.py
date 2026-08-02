"""TDD tests for D-21: Git worktree lease tracking and cleanup.

Worktree operations must own worktrees through namespace/lease files,
clean up in normal/failure paths, and reconcile expired leases after
crashes without touching active or foreign worktrees.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from general_ludd.git_automation.worktree_lease import (
    check_worktree_lease,
    cleanup_expired_leases,
    release_worktree_lease,
    worktree_lease_info,
    write_worktree_lease,
)


@pytest.fixture
def lease_repo(tmp_path: Path) -> Path:
    """Simulates a git repo root; leases are stored at .gludd/leases/ inside."""
    return tmp_path


@pytest.fixture
def lease_dir(lease_repo: Path) -> Path:
    """The actual leases directory: <repo>/.gludd/leases/."""
    d = lease_repo / ".gludd" / "leases"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# write_worktree_lease
# ---------------------------------------------------------------------------


def test_write_lease_creates_file(lease_repo: Path, lease_dir: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-test", ttl_seconds=3600)
    lease_file = lease_dir / "agent-test.lease.json"
    assert lease_file.exists()


def test_write_lease_contains_expected_fields(lease_repo: Path, lease_dir: Path) -> None:
    before = time.time()
    write_worktree_lease(str(lease_repo), branch="agent-foo", ttl_seconds=1800)
    after = time.time()
    lease_file = lease_dir / "agent-foo.lease.json"
    raw = json.loads(lease_file.read_text())
    assert raw["branch"] == "agent-foo"
    assert raw["owner_pid"] == os.getpid()
    assert raw["ttl_seconds"] == 1800
    assert before - 2 <= raw["created_at"] <= after + 2


def test_write_lease_has_mode_0600(lease_repo: Path, lease_dir: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-bar", ttl_seconds=3600)
    lease_file = lease_dir / "agent-bar.lease.json"
    mode = lease_file.stat().st_mode
    assert mode & 0o777 == 0o600


def test_write_lease_overwrites_existing(lease_repo: Path, lease_dir: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-dup", ttl_seconds=300)
    first_mtime = (lease_dir / "agent-dup.lease.json").stat().st_mtime
    time.sleep(0.1)
    write_worktree_lease(str(lease_repo), branch="agent-dup", ttl_seconds=600)
    second_mtime = (lease_dir / "agent-dup.lease.json").stat().st_mtime
    assert second_mtime > first_mtime
    raw = json.loads((lease_dir / "agent-dup.lease.json").read_text())
    assert raw["ttl_seconds"] == 600


def test_write_lease_rejects_escape_path(lease_repo: Path) -> None:
    with pytest.raises(ValueError, match="escape"):
        write_worktree_lease(str(lease_repo), branch="../escape", ttl_seconds=3600)


def test_write_lease_rejects_absolute_path(lease_repo: Path) -> None:
    with pytest.raises(ValueError, match="escape"):
        write_worktree_lease(str(lease_repo), branch="/etc/passwd", ttl_seconds=3600)


# ---------------------------------------------------------------------------
# check_worktree_lease
# ---------------------------------------------------------------------------


def test_check_active_lease_returns_owned(lease_repo: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-active", ttl_seconds=3600)
    result = check_worktree_lease(str(lease_repo), branch="agent-active")
    assert result is True


def test_check_expired_lease_returns_false(lease_repo: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-expired", ttl_seconds=0)
    time.sleep(0.1)
    result = check_worktree_lease(str(lease_repo), branch="agent-expired")
    assert result is False


def test_check_missing_lease_returns_false(lease_repo: Path) -> None:
    result = check_worktree_lease(str(lease_repo), branch="nonexistent-branch")
    assert result is False


def test_check_lease_same_process(lease_repo: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-self", ttl_seconds=3600)
    result = check_worktree_lease(str(lease_repo), branch="agent-self")
    assert result is True


# ---------------------------------------------------------------------------
# release_worktree_lease
# ---------------------------------------------------------------------------


def test_release_lease_removes_file(lease_repo: Path, lease_dir: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-release-me", ttl_seconds=3600)
    assert (lease_dir / "agent-release-me.lease.json").exists()
    release_worktree_lease(str(lease_repo), branch="agent-release-me")
    assert not (lease_dir / "agent-release-me.lease.json").exists()


def test_release_missing_lease_no_error(lease_repo: Path) -> None:
    release_worktree_lease(str(lease_repo), branch="never-existed")


def test_release_after_release_no_error(lease_repo: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-double-release", ttl_seconds=3600)
    release_worktree_lease(str(lease_repo), branch="agent-double-release")
    release_worktree_lease(str(lease_repo), branch="agent-double-release")


# ---------------------------------------------------------------------------
# cleanup_expired_leases
# ---------------------------------------------------------------------------


def test_cleanup_removes_expired_leases(lease_repo: Path, lease_dir: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-old", ttl_seconds=0)
    write_worktree_lease(str(lease_repo), branch="agent-fresh", ttl_seconds=3600)
    time.sleep(0.1)
    removed = cleanup_expired_leases(str(lease_repo))
    assert removed >= 1
    assert not (lease_dir / "agent-old.lease.json").exists()
    assert (lease_dir / "agent-fresh.lease.json").exists()


def test_cleanup_empty_dir_returns_zero(lease_repo: Path) -> None:
    assert cleanup_expired_leases(str(lease_repo)) == 0


def test_cleanup_keeps_active_leases(lease_repo: Path, lease_dir: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-keep", ttl_seconds=86400)
    removed = cleanup_expired_leases(str(lease_repo))
    assert removed == 0
    assert (lease_dir / "agent-keep.lease.json").exists()


def test_cleanup_handles_corrupt_lease_file(lease_repo: Path, lease_dir: Path) -> None:
    corrupt = lease_dir / "agent-corrupt.lease.json"
    corrupt.write_text("not valid json")
    removed = cleanup_expired_leases(str(lease_repo))
    assert removed >= 1
    assert not corrupt.exists()


# ---------------------------------------------------------------------------
# worktree_lease_info
# ---------------------------------------------------------------------------


def test_lease_info_returns_active_leases(lease_repo: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-a", ttl_seconds=3600)
    write_worktree_lease(str(lease_repo), branch="agent-b", ttl_seconds=3600)
    info = worktree_lease_info(str(lease_repo))
    assert len(info) == 2
    branches = {item["branch"] for item in info}
    assert branches == {"agent-a", "agent-b"}


def test_lease_info_excludes_expired(lease_repo: Path) -> None:
    write_worktree_lease(str(lease_repo), branch="agent-expired-info", ttl_seconds=0)
    time.sleep(0.1)
    info = worktree_lease_info(str(lease_repo))
    assert len(info) == 1
    assert info[0]["expired"] is True


def test_lease_info_empty_dir(lease_repo: Path) -> None:
    assert worktree_lease_info(str(lease_repo)) == []


# ---------------------------------------------------------------------------
# Path rejection safety
# ---------------------------------------------------------------------------


def test_lease_rejects_branch_with_traversal(lease_repo: Path) -> None:
    bad_branches = [
        "../../../etc/passwd",
        "foo/../../../bar",
        "..",
        ".",
        "/absolute/path",
    ]
    for branch in bad_branches:
        with pytest.raises(ValueError, match="escape"):
            write_worktree_lease(str(lease_repo), branch=branch, ttl_seconds=3600)
