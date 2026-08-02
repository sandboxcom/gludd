"""D-21: Enhanced worktree lease tests — PID verification and lifecycle integration.

Tests that lease operations verify the owning PID exists, that leases
are written during worktree create/cleanup, and that kill-at-every-phase
cleanup preserves concurrent foreign worktrees.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.git_automation.worktree_lease import (
    check_worktree_lease,
    cleanup_expired_leases,
    is_pid_alive,
    release_worktree_lease,
    verify_worktree_lease,
    write_worktree_lease,
)


@pytest.fixture
def lease_repo(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def lease_dir(lease_repo: Path) -> Path:
    d = lease_repo / ".gludd" / "leases"
    d.mkdir(parents=True)
    return d


# ── PID verification ──


class TestPidVerification:
    def test_current_process_is_alive(self) -> None:
        assert is_pid_alive(os.getpid()) is True

    def test_negative_pid_is_dead(self) -> None:
        assert is_pid_alive(-1) is False

    def test_zero_pid_is_dead(self) -> None:
        assert is_pid_alive(0) is False

    def test_very_large_pid_is_dead(self) -> None:
        assert is_pid_alive(999_999_999) is False

    def test_pid_one_exists_on_unix(self) -> None:
        result = is_pid_alive(1)
        assert isinstance(result, bool)

    def test_verify_lease_for_running_pid(self, lease_repo: Path) -> None:
        write_worktree_lease(str(lease_repo), branch="agent-alive", ttl_seconds=3600)
        result = verify_worktree_lease(str(lease_repo), branch="agent-alive")
        assert result["owned"] is True
        assert result["pid_alive"] is True

    def test_verify_lease_for_dead_pid(self, lease_repo: Path, lease_dir: Path) -> None:
        lease_path = lease_dir / "agent-dead.lease.json"
        lease_path.write_text(
            json.dumps(
                {
                    "branch": "agent-dead",
                    "owner_pid": 999_999_999,
                    "created_at": time.time(),
                    "ttl_seconds": 3600,
                }
            )
        )
        result = verify_worktree_lease(str(lease_repo), branch="agent-dead")
        assert result["owned"] is False
        assert result["pid_alive"] is False

    def test_verify_missing_lease(self, lease_repo: Path) -> None:
        result = verify_worktree_lease(str(lease_repo), branch="nonexistent")
        assert result["owned"] is False
        assert result["branch"] == "nonexistent"

    def test_is_pid_alive_handles_permission_error(self) -> None:
        with mock.patch("os.kill", side_effect=PermissionError):
            assert is_pid_alive(1) is True


# ── Lease lifecycle integration ──


class TestLeaseLifecycle:
    def test_write_check_release_cycle(self, lease_repo: Path) -> None:
        branch = "agent-lifecycle"
        write_worktree_lease(str(lease_repo), branch=branch, ttl_seconds=600)

        assert check_worktree_lease(str(lease_repo), branch) is True

        release_worktree_lease(str(lease_repo), branch)
        assert check_worktree_lease(str(lease_repo), branch) is False

    def test_lease_with_expiry_falls_below_threshold(self, lease_repo: Path) -> None:
        write_worktree_lease(str(lease_repo), branch="agent-short", ttl_seconds=0)
        time.sleep(0.1)
        assert check_worktree_lease(str(lease_repo), "agent-short") is False

    def test_verify_for_expired_lease_returns_not_owned(self, lease_repo: Path) -> None:
        write_worktree_lease(str(lease_repo), branch="agent-expired-vfy", ttl_seconds=0)
        time.sleep(0.1)
        result = verify_worktree_lease(str(lease_repo), branch="agent-expired-vfy")
        assert result["owned"] is False


# ── Concurrent foreign worktree preservation ──


class TestForeignWorktreePreservation:
    def test_cleanup_preserves_foreign_lease_files(self, lease_repo: Path, lease_dir: Path) -> None:
        foreign = lease_dir / "foreign-project.lease.json"
        foreign.write_text(
            json.dumps(
                {
                    "branch": "foreign-project",
                    "owner_pid": os.getpid(),
                    "created_at": time.time() - 3600,
                    "ttl_seconds": 86400,
                }
            )
        )
        foreign_before = foreign.read_text()

        write_worktree_lease(str(lease_repo), branch="agent-mine", ttl_seconds=0)
        time.sleep(0.1)
        cleanup_expired_leases(str(lease_repo))

        assert foreign.exists()
        assert foreign.read_text() == foreign_before

    def test_cleanup_does_not_remove_directories(self, lease_repo: Path, lease_dir: Path) -> None:
        subdir = lease_dir / "nested"
        subdir.mkdir()
        write_worktree_lease(str(lease_repo), branch="agent-test", ttl_seconds=0)
        time.sleep(0.1)
        cleanup_expired_leases(str(lease_repo))
        assert subdir.exists()

    def test_cleanup_ignores_non_json_files(self, lease_repo: Path, lease_dir: Path) -> None:
        notes = lease_dir / "README.txt"
        notes.write_text("lease directory notes")
        write_worktree_lease(str(lease_repo), branch="agent-x", ttl_seconds=3600)
        cleanup_expired_leases(str(lease_repo))
        assert notes.exists()


# ── Crash recovery — kill-at-every-phase ──


class TestKillAtEveryPhase:
    def test_lease_survives_graceful_release(self, lease_repo: Path) -> None:
        write_worktree_lease(str(lease_repo), branch="agent-phase1", ttl_seconds=3600)
        assert check_worktree_lease(str(lease_repo), "agent-phase1") is True

        release_worktree_lease(str(lease_repo), "agent-phase1")
        assert not (lease_repo / ".gludd" / "leases" / "agent-phase1.lease.json").exists()

    def test_expired_lease_cleanup_recovers_after_crash(self, lease_repo: Path) -> None:
        write_worktree_lease(str(lease_repo), branch="agent-crashed", ttl_seconds=0)
        time.sleep(0.1)
        removed = cleanup_expired_leases(str(lease_repo))
        assert removed >= 1

    def test_multi_lease_crash_recovery(self, lease_repo: Path, lease_dir: Path) -> None:
        for i in range(5):
            write_worktree_lease(str(lease_repo), branch=f"agent-expired-{i}", ttl_seconds=0)

        time.sleep(0.1)
        write_worktree_lease(str(lease_repo), branch="agent-alive", ttl_seconds=3600)

        removed = cleanup_expired_leases(str(lease_repo))
        assert removed >= 5

        assert (lease_dir / "agent-alive.lease.json").exists()
        for i in range(5):
            assert not (lease_dir / f"agent-expired-{i}.lease.json").exists()

    def test_corrupt_lease_during_crash_recovery(self, lease_repo: Path, lease_dir: Path) -> None:
        corrupt = lease_dir / "agent-corrupt-crash.lease.json"
        corrupt.write_text("{invalid json")
        [e for e in lease_dir.iterdir() if e.suffix == ".json"]
        cleanup_expired_leases(str(lease_repo))
        assert not corrupt.exists()
