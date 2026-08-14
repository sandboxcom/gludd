"""Deep integration-behavior tests for git_automation module.

Covers end-to-end scenarios across worktree lifecycle, branch management,
commit verification, merge conflicts, remote sync, and lock management.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.git_automation.batch_push import (
    _ci_in_flight,
    _count_unpushed,
    batch_push,
)
from general_ludd.git_automation.locking import (
    _break_if_stale,
    _get_inprocess_lock,
    _normalize,
    git_repo_lock,
)
from general_ludd.git_automation.ship_commit import (
    ShipCommitError,
    gate_is_green,
    ship_commit,
)
from general_ludd.git_automation.types import (
    MergeResult,
    VerifyRemoteResult,
)
from general_ludd.git_automation.verify_remote import (
    verify_remote,
)
from general_ludd.git_automation.worktree import (
    WorktreeHealthViolation,
    worktree_cleanup,
    worktree_create,
    worktree_health_check,
    worktree_merge,
    worktree_merge_all,
)
from general_ludd.git_automation.worktree_lease import (
    check_worktree_lease,
    cleanup_expired_leases,
    is_pid_alive,
    release_worktree_lease,
    verify_worktree_lease,
    worktree_lease_info,
    write_worktree_lease,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _git_success(stdout: str = "", rc: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = rc
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _git_fail(stderr: str = "fatal: something went wrong", rc: int = 1) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = rc
    proc.stdout = ""
    proc.stderr = stderr
    return proc


def _mp_git_lock_worker(order_list, rp, name, hold):
    """Module-level worker for multiprocessing cross-process lock test."""
    with git_repo_lock(rp, timeout=10.0, stale_after=60.0):
        order_list.append(f"{name}:enter")
        time.sleep(hold)
        order_list.append(f"{name}:exit")


_WT_MAIN_ONLY = """worktree /Users/shawnwilson/gludd
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development
"""

_WT_ONE_AGENT = """worktree /Users/shawnwilson/gludd
HEAD abc123def4567890123456789012345678abcdef
branch refs/heads/development

worktree /tmp/gludd-worktrees/agent-deep
HEAD def4567890123456789012345678abcdef012345
branch refs/heads/agent-deep
"""


# ── 1. Worktree lifecycle (deep) ─────────────────────────────────────────────


class TestWorktreeLifecycleDeep:
    """End-to-end worktree create → merge → cleanup with mocked git."""

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_create_then_merge_then_cleanup(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(),  # worktree add -b (create)
        ]
        create_result = worktree_create("/Users/shawnwilson/gludd", "agent-lifecycle")
        assert create_result.success is True
        assert create_result.branch == "agent-lifecycle"

        mock_run.side_effect = [
            _git_success("development"),  # rev-parse HEAD
            _git_success(),  # checkout development
            _git_success(),  # merge --no-ff
            _git_success("development"),  # (finally: checkout back)
        ]
        merge_result = worktree_merge("/Users/shawnwilson/gludd", "agent-lifecycle")
        assert merge_result.success is True
        assert merge_result.strategy == "no-ff"

        mock_run.side_effect = [
            _git_success(),  # worktree remove
            _git_success(),  # prune
            _git_success(),  # branch -d
        ]
        cleanup_result = worktree_cleanup("/Users/shawnwilson/gludd", "agent-lifecycle")
        assert cleanup_result["success"] is True
        assert cleanup_result["cleaned"] is True
        assert cleanup_result["branch_removed"] is True

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_create_worktree_from_specific_base_branch(self, mock_run: MagicMock):
        mock_run.side_effect = [_git_success()]
        result = worktree_create(
            "/Users/shawnwilson/gludd",
            "agent-from-dev",
            base_branch="development",
        )
        assert result.success is True
        args = mock_run.call_args[0]
        assert "development" in args

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_merge_with_conflict_detection_and_abort(self, mock_run: MagicMock):
        conflict_stderr = (
            "CONFLICT (content): Merge conflict in daemon.py\n"
            "Automatic merge failed; fix conflicts and then commit the result.\n"
        )
        mock_run.side_effect = [
            _git_success("development"),  # rev-parse HEAD
            _git_success(),  # checkout development
            _git_fail(rc=1, stderr=conflict_stderr),  # merge fails
            _git_success(),  # merge --abort
            _git_success("development"),  # finally checkout
        ]
        result = worktree_merge("/Users/shawnwilson/gludd", "agent-conflict")
        assert result.success is False
        assert len(result.conflicts) >= 1
        assert "CONFLICT" in result.message

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_merge_rejects_leading_dash_target_branch(self, mock_run: MagicMock):
        result = worktree_merge("/Users/shawnwilson/gludd", "agent-ok", target_branch="--evil")
        assert result.success is False
        assert "-" in result.message

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_cleanup_handles_unlock_if_remove_fails(self, mock_run: MagicMock):
        with patch("os.path.isdir", return_value=True):
            mock_run.side_effect = [
                _git_fail(rc=128, stderr="fatal: not a working tree"),  # remove fails
                _git_success(),  # unlock
                _git_success(),  # prune
                _git_success(),  # branch -d
            ]
            result = worktree_cleanup("/Users/shawnwilson/gludd", "agent-stuck")
            assert result["success"] is True
            all_first_args = [str(c.kwargs.get("cwd", "")) + ":" + str(c.args[:2]) for c in mock_run.call_args_list]
            any_unlock = any("unlock" in s for s in all_first_args)
            assert any_unlock, f"unlock not found in calls: {all_first_args}"

    def test_worktree_create_rejects_path_traversal(self):
        result = worktree_create("/Users/shawnwilson/gludd", "../escape")
        assert result.success is False
        assert "escape" in result.message.lower()

    def test_worktree_create_rejects_leading_dash_branch(self):
        result = worktree_create("/Users/shawnwilson/gludd", "--upload-pack=evil")
        assert result.success is False
        assert "-" in result.message


# ── 2. Branch management (deep) ──────────────────────────────────────────────


class TestBranchManagementDeep:
    """Feature branch lifecycle and rebranch operations."""

    def test_feature_start_full_lifecycle(self):
        from general_ludd.git_automation.feature_branch import feature_done, feature_start
        from general_ludd.git_automation.repo import GitAutomation
        from general_ludd.git_automation.types import GitStateResult

        git = MagicMock(spec=GitAutomation, repo_path="/repo")
        git.current_branch.return_value = "master"
        git.list_branches.return_value = ["master"]
        git.create_branch.return_value = "feature/my-feat"

        branch = feature_start(git=git, name="my-feat")
        assert branch == "feature/my-feat"
        git.create_branch.assert_called_once_with("feature/my-feat")

        git.list_branches.return_value = ["master", "feature/my-feat"]
        state = MagicMock(spec=GitStateResult, success=True)
        git.workflow_state.return_value = state
        git.merge_branch.return_value = MergeResult(success=True, strategy="no-ff")

        result = feature_done(git=git, name="feature/my-feat", target="master")
        assert result["success"] is True
        git.delete_branch.assert_called_once_with("feature/my-feat")

    def test_feature_done_requires_clean_tree(self):
        from general_ludd.git_automation.feature_branch import feature_done
        from general_ludd.git_automation.repo import GitAutomation
        from general_ludd.git_automation.types import GitStateResult

        git = MagicMock(spec=GitAutomation)
        git.list_branches.return_value = ["master", "feature/dirty"]
        state = MagicMock(spec=GitStateResult, success=False, errors=["2 dirty path(s)"])
        git.workflow_state.return_value = state

        with pytest.raises(ValueError, match="not clean"):
            feature_done(git=git, name="feature/dirty")

    def test_feature_done_handles_merge_conflict(self):
        from general_ludd.git_automation.feature_branch import feature_done
        from general_ludd.git_automation.repo import GitAutomation
        from general_ludd.git_automation.types import GitStateResult

        git = MagicMock(spec=GitAutomation, repo_path="/repo")
        git.list_branches.return_value = ["master", "feature/conflict"]
        state = MagicMock(spec=GitStateResult, success=True)
        git.workflow_state.return_value = state
        git.merge_branch.return_value = MergeResult(
            success=False,
            strategy="no-ff",
            message="CONFLICT in daemon.py",
            conflicts=["daemon.py"],
        )

        with pytest.raises(RuntimeError, match="merge of"):
            feature_done(git=git, name="feature/conflict")

    def test_feature_done_rejects_nonexistent_branch(self):
        from general_ludd.git_automation.feature_branch import feature_done
        from general_ludd.git_automation.repo import GitAutomation

        git = MagicMock(spec=GitAutomation)
        git.list_branches.return_value = ["master"]

        with pytest.raises(ValueError, match="not found"):
            feature_done(git=git, name="feature/ghost")


# ── 3. Commit verification (deep) ────────────────────────────────────────────


class TestCommitVerificationDeep:
    """Gate-gated commit, test-and-commit, and gate freshness."""

    def test_gate_is_green_with_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            assert gate_is_green(os.path.join(d, ".gate-status")) is False

    def test_gate_is_green_with_passed_marker(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".gate-status")
            Path(p).write_text("=== GATE: PASSED ===\nlint PASS\ntypecheck PASS\n")
            assert gate_is_green(p) is True

    def test_gate_is_green_with_failed_marker(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".gate-status")
            Path(p).write_text("=== GATE: FAILED ===\ntests FAIL\n")
            assert gate_is_green(p) is False

    def test_gate_is_green_with_passed_then_failed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".gate-status")
            Path(p).write_text("=== GATE: PASSED ===\nmid\n=== GATE: FAILED ===\n")
            assert gate_is_green(p) is False

    def test_ship_commit_blocks_on_red_gate(self):
        ga = MagicMock()
        with tempfile.TemporaryDirectory() as d:
            gp = os.path.join(d, ".gate-status")
            Path(gp).write_text("=== GATE: FAILED ===\n")
            with pytest.raises(ShipCommitError, match="red"):
                ship_commit("msg", git=ga, repo_root=d, gate_path_override=gp)
            ga.commit.assert_not_called()

    def test_ship_commit_allows_skip_gate_for_meta(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"
        sha = ship_commit("meta: bump version", git=ga, skip_gate=True)
        assert sha == "abc1234"
        ga.commit.assert_called_once()

    def test_ship_commit_stages_specific_files(self):
        ga = MagicMock()
        ga.commit.return_value = "abc1234"
        with tempfile.TemporaryDirectory() as d:
            gp = os.path.join(d, ".gate-status")
            Path(gp).write_text("=== GATE: PASSED ===\n")
            sha = ship_commit("msg", files=["a.py", "b.py"], git=ga, repo_root=d, gate_path_override=gp)
            assert sha == "abc1234"
            add_calls = [c for c in ga._run_git.call_args_list if c.args[0] == "add"]
            assert len(add_calls) == 2

    def test_ship_commit_with_push_flag(self):
        ga = MagicMock()
        ga.commit.return_value = "def5678"
        with tempfile.TemporaryDirectory() as d:
            gp = os.path.join(d, ".gate-status")
            Path(gp).write_text("=== GATE: PASSED ===\n")
            sha = ship_commit("msg", git=ga, push=True, repo_root=d, gate_path_override=gp)
            ga.push.assert_called_once()
            assert sha == "def5678"


# ── 4. Merge conflict detection (deep) ───────────────────────────────────────


class TestMergeConflictDetectionDeep:
    """Merge conflict detection, abort, and reporting."""

    def test_merge_conflict_returns_conflicts_list(self):
        result = MergeResult(
            success=False,
            strategy="no-ff",
            message="CONFLICT (content): Merge conflict in src/daemon.py",
            conflicts=["src/daemon.py"],
        )
        assert result.success is False
        assert len(result.conflicts) == 1
        assert result.conflicts[0] == "src/daemon.py"

    def test_merge_success_has_empty_conflicts(self):
        result = MergeResult(success=True, strategy="no-ff", message="Merge made.")
        assert result.success is True
        assert result.conflicts == []

    def test_merge_conflict_multiple_files(self):
        result = MergeResult(
            success=False,
            strategy="no-ff",
            message="CONFLICT in a.py\nCONFLICT in b.py",
            conflicts=["a.py", "b.py"],
        )
        assert len(result.conflicts) == 2

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_merge_aborts_on_called_process_error(self, mock_run: MagicMock):
        import subprocess as sp

        mock_run.side_effect = [
            sp.CalledProcessError(
                128,
                ["git", "rev-parse"],
                stderr="fatal: not a git repository",
            ),
            _git_success(),  # merge --abort in except handler
        ]
        result = worktree_merge("/Users/shawnwilson/gludd", "agent-corrupt")
        assert result.success is False
        assert "fatal" in result.message.lower() or "not a git repository" in result.message.lower()


# ── 5. Remote sync operations (deep) ─────────────────────────────────────────


class TestRemoteSyncDeep:
    """Verify remote, batch push with threshold and CI checks."""

    def test_verify_remote_matched_sha(self):
        result = VerifyRemoteResult(
            status="VERIFIED",
            remote_sha="abc123",
            expected_sha="abc123",
            remote="sandboxcom",
            ref="refs/heads/master",
        )
        assert result.status == "VERIFIED"

    def test_verify_remote_mismatched_sha(self):
        result = VerifyRemoteResult(
            status="MISMATCH",
            remote_sha="abc",
            expected_sha="def",
            remote="sandboxcom",
            ref="refs/heads/master",
            message="REMOTE MISMATCH: remote=abc expected=def",
        )
        assert result.status == "MISMATCH"
        assert "MISMATCH" in result.message

    def test_verify_remote_unreachable(self):
        result = VerifyRemoteResult(
            status="UNREACHABLE",
            remote_sha="",
            expected_sha="abc",
            remote="sandboxcom",
            ref="refs/heads/master",
            message="git ls-remote timed out",
        )
        assert result.status == "UNREACHABLE"

    def test_verify_remote_rejects_leading_dash_remote(self):
        with pytest.raises(ValueError, match="begins with '-'"):
            verify_remote("-evil", "master", "abc123")

    def test_verify_remote_rejects_leading_dash_branch(self):
        with pytest.raises(ValueError, match="begins with '-'"):
            verify_remote("sandboxcom", "--force", "abc123")

    def test_verify_remote_rejects_invalid_ref_type(self):
        with pytest.raises(ValueError, match="ref_type"):
            verify_remote("sandboxcom", "master", "abc123", ref_type="invalid")

    def test_batch_push_below_threshold_blocks(self):
        with (
            patch("general_ludd.git_automation.batch_push._count_unpushed", return_value=2),
            patch("general_ludd.git_automation.batch_push._ci_in_flight", return_value=False),
        ):
            result = batch_push("/repo", remote="sandboxcom", branch="master", threshold=5)
            assert result.pushed is False
            assert result.reason == "below_threshold"
            assert result.unpushed_count == 2

    def test_batch_push_force_bypasses_threshold(self):
        with (
            patch("general_ludd.git_automation.batch_push._count_unpushed", return_value=1),
            patch("general_ludd.git_automation.batch_push._do_push", return_value=True),
            patch("general_ludd.git_automation.batch_push._verify_remote", return_value="abc123"),
        ):
            result = batch_push("/repo", remote="sandboxcom", branch="master", threshold=5, force=True)
            assert result.pushed is True
            assert result.reason == "force_override"

    def test_batch_push_ci_in_flight_blocks(self):
        with (
            patch("general_ludd.git_automation.batch_push._count_unpushed", return_value=10),
            patch("general_ludd.git_automation.batch_push._ci_in_flight", return_value=True),
        ):
            result = batch_push("/repo", remote="sandboxcom", branch="master", threshold=5)
            assert result.pushed is False
            assert result.reason == "ci_in_flight"

    def test_batch_push_verify_remote_failure(self):
        with (
            patch("general_ludd.git_automation.batch_push._count_unpushed", return_value=10),
            patch("general_ludd.git_automation.batch_push._ci_in_flight", return_value=False),
            patch("general_ludd.git_automation.batch_push._do_push", return_value=True),
            patch("general_ludd.git_automation.batch_push._verify_remote", return_value=""),
        ):
            result = batch_push("/repo", remote="sandboxcom", branch="master", threshold=5)
            assert result.pushed is True
            assert result.verified is False
            assert result.remote_sha == ""

    def test_batch_push_push_failure(self):
        with (
            patch("general_ludd.git_automation.batch_push._count_unpushed", return_value=10),
            patch("general_ludd.git_automation.batch_push._ci_in_flight", return_value=False),
            patch("general_ludd.git_automation.batch_push._do_push", return_value=False),
        ):
            result = batch_push("/repo", remote="sandboxcom", branch="master", threshold=5)
            assert result.pushed is False
            assert result.reason == "push_failed"

    def test_count_unpushed_zero_on_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _count_unpushed("/repo", "origin", "main") == 0

    def test_ci_in_flight_false_on_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _ci_in_flight("master") is False


# ── 6. Lock file management (deep) ───────────────────────────────────────────


class TestLockFileManagementDeep:
    """In-process lock registry, cross-process flock, stale detection, re-entrancy."""

    def test_normalize_canonicalizes_path(self):
        a = _normalize(".")
        b = _normalize(os.path.abspath("."))
        assert a == b

    def test_inprocess_lock_same_key_returns_same_object(self):
        a = _get_inprocess_lock("repo-X")
        b = _get_inprocess_lock("repo-X")
        assert a is b

    def test_inprocess_lock_different_keys_different_objects(self):
        a = _get_inprocess_lock("repo-A")
        b = _get_inprocess_lock("repo-B")
        assert a is not b

    def test_inprocess_lock_is_reentrant(self):
        lock = _get_inprocess_lock("repo-reentrant")
        lock.acquire()
        acquired = lock.acquire(blocking=False)
        assert acquired is True
        lock.release()
        lock.release()

    def test_inprocess_lock_serializes_within_process(self):
        lock = _get_inprocess_lock("repo-serial")
        results: list[int] = []
        lock.acquire()

        def _worker():
            lock.acquire()
            results.append(2)
            lock.release()

        results.append(1)
        t = threading.Thread(target=_worker)
        t.start()
        time.sleep(0.1)
        assert results == [1]
        lock.release()
        t.join(timeout=1)
        assert results == [1, 2]

    def test_cross_process_lock_via_multiprocessing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = os.path.join(tmpdir, "test-repo")
            os.mkdir(repo_path)
            git_path = os.path.join(repo_path, ".git")
            os.mkdir(git_path)

            order = multiprocessing.Manager().list()

            p1 = multiprocessing.Process(target=_mp_git_lock_worker, args=(order, repo_path, "first", 0.2))
            p2 = multiprocessing.Process(target=_mp_git_lock_worker, args=(order, repo_path, "second", 0.1))
            p1.start()
            time.sleep(0.05)
            p2.start()
            p1.join(timeout=5)
            p2.join(timeout=5)
            events = list(order)
            assert sorted(events) == [
                "first:enter",
                "first:exit",
                "second:enter",
                "second:exit",
            ]
            for enter, leave in zip(events[::2], events[1::2], strict=True):
                assert enter.endswith(":enter")
                assert leave == enter.replace(":enter", ":exit")

    def test_stale_lock_metadata_preserves_mutex_inode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            Path(lock_path).write_text("")
            old_mtime = time.time() - 600
            os.utime(lock_path, (old_mtime, old_mtime))
            _break_if_stale(lock_path, stale_after=300)
            assert os.path.exists(lock_path)

    def test_fresh_lock_not_broken(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            Path(lock_path).write_text("")
            _break_if_stale(lock_path, stale_after=300)
            assert os.path.exists(lock_path)

    def test_git_repo_lock_reentrant_no_deadlock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = os.path.join(tmpdir, "reentrant-repo")
            os.mkdir(repo_path)
            git_path = os.path.join(repo_path, ".git")
            os.mkdir(git_path)
            with git_repo_lock(repo_path, timeout=5.0), git_repo_lock(repo_path, timeout=5.0):
                assert True

    def test_lock_is_fully_reentrant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = os.path.join(tmpdir, "reentrant2-repo")
            os.mkdir(repo_path)
            git_path = os.path.join(repo_path, ".git")
            os.mkdir(git_path)
            with (
                git_repo_lock(repo_path, timeout=5.0),
                git_repo_lock(repo_path, timeout=5.0),
                git_repo_lock(repo_path, timeout=5.0),
            ):
                assert True  # triple re-entrancy works

    def test_lock_acquires_uncontended_with_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = os.path.join(tmpdir, "timeout-repo")
            os.mkdir(repo_path)
            git_path = os.path.join(repo_path, ".git")
            os.mkdir(git_path)
            with git_repo_lock(repo_path, timeout=0.5, stale_after=300):
                assert True  # should acquire instantly when uncontended


# ── 7. Worktree lease management (deep) ──────────────────────────────────────


class TestWorktreeLeaseDeep:
    """Lease write, check, verify, release, expiry cleanup."""

    def test_write_and_check_lease(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lease_path = write_worktree_lease(tmpdir, "agent-lease", ttl_seconds=60)
            assert lease_path.exists()
            assert check_worktree_lease(tmpdir, "agent-lease") is True

    def test_expired_lease_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_worktree_lease(tmpdir, "agent-expired", ttl_seconds=1)
            time.sleep(1.1)
            assert check_worktree_lease(tmpdir, "agent-expired") is False

    def test_missing_lease_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert check_worktree_lease(tmpdir, "nonexistent") is False

    def test_release_lease_removes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lease_path = write_worktree_lease(tmpdir, "agent-release", ttl_seconds=60)
            release_worktree_lease(tmpdir, "agent-release")
            assert not lease_path.exists()

    def test_verify_lease_owned_when_pid_alive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_worktree_lease(tmpdir, "agent-alive", ttl_seconds=60)
            result = verify_worktree_lease(tmpdir, "agent-alive")
            assert result["owned"] is True
            assert result["branch"] == "agent-alive"
            assert result["owner_pid"] == os.getpid()

    def test_verify_lease_not_owned_when_pid_dead(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lease_dir = Path(tmpdir) / ".gludd" / "leases"
            lease_dir.mkdir(parents=True, exist_ok=True)
            lease_path = lease_dir / "agent-dead.lease.json"
            lease_path.write_text(
                json.dumps(
                    {
                        "branch": "agent-dead",
                        "owner_pid": 99999,
                        "created_at": time.time(),
                        "ttl_seconds": 60,
                    }
                )
            )
            result = verify_worktree_lease(tmpdir, "agent-dead")
            assert result["owned"] is False

    def test_cleanup_expired_leases_removes_old(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_worktree_lease(tmpdir, "agent-old", ttl_seconds=1)
            write_worktree_lease(tmpdir, "agent-fresh", ttl_seconds=600)
            time.sleep(1.1)
            removed = cleanup_expired_leases(tmpdir)
            assert removed >= 1

    def test_worktree_lease_info_returns_active_leases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_worktree_lease(tmpdir, "agent-info", ttl_seconds=60)
            info = worktree_lease_info(tmpdir)
            assert len(info) == 1
            assert info[0]["branch"] == "agent-info"
            assert info[0]["expired"] is False

    def test_is_pid_alive_current_process(self):
        assert is_pid_alive(os.getpid()) is True

    def test_is_pid_alive_non_positive(self):
        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False

    def test_is_pid_alive_nonexistent(self):
        assert is_pid_alive(99999) is False


# ── 8. Worktree health check (deep) ──────────────────────────────────────────


class TestWorktreeHealthDeep:
    """WorktreeHealthViolation and health_check scenarios."""

    def test_violation_has_all_fields(self):
        v = WorktreeHealthViolation(
            worktree_path="/tmp/wt",
            branch="agent-x",
            reason="Stale >24h and NOT merged into development",
            severity="error",
        )
        assert v.worktree_path == "/tmp/wt"
        assert v.branch == "agent-x"
        assert v.severity == "error"
        assert "Stale" in repr(v)

    def test_warning_violation_for_merged_stale(self):
        v = WorktreeHealthViolation(
            worktree_path="/tmp/wt",
            branch="agent-x",
            reason="Stale >24h and already merged — cleanup needed",
            severity="warning",
        )
        assert v.severity == "warning"

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_health_check_skips_main_checkout(self, mock_run: MagicMock):
        mock_run.return_value = _git_success(_WT_MAIN_ONLY)
        violations = worktree_health_check("/Users/shawnwilson/gludd")
        assert violations == []

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_health_check_flags_stale_unmerged(self, mock_run: MagicMock):
        two_days = 48 * 3600
        mock_run.side_effect = [
            _git_success(_WT_ONE_AGENT),  # worktree list
            _git_fail(rc=1),  # merge-base --is-ancestor (not merged)
            _git_success("def456\trefs/heads/agent-deep"),  # ls-remote
        ]
        with patch("general_ludd.git_automation.worktree._get_tree_age_seconds", return_value=two_days):
            violations = worktree_health_check(
                "/Users/shawnwilson/gludd",
                max_age_hours=24,
                remote_name="sandboxcom",
            )
        assert len(violations) >= 1
        assert violations[0].severity == "error"

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_health_check_flags_missing_remote_branch(self, mock_run: MagicMock):
        one_hour = 3600
        mock_run.side_effect = [
            _git_success(_WT_ONE_AGENT),  # worktree list
            _git_success(),  # merge-base --is-ancestor returns 0 (merged)
            _git_success(""),  # ls-remote: no output (branch not on remote)
        ]
        with patch("general_ludd.git_automation.worktree._get_tree_age_seconds", return_value=one_hour):
            violations = worktree_health_check(
                "/Users/shawnwilson/gludd",
                max_age_hours=24,
                remote_name="sandboxcom",
            )
        assert any(v.severity == "warning" and "does not exist on remote" in v.reason for v in violations)


# ── 9. Merge all and cleanup (deep) ──────────────────────────────────────────


class TestMergeAllDeep:
    """worktree_merge_all bulk operations."""

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_merge_all_zero_worktrees(self, mock_run: MagicMock):
        mock_run.return_value = _git_success(_WT_MAIN_ONLY)
        result = worktree_merge_all("/Users/shawnwilson/gludd")
        assert result["total"] == 0
        assert result["merged"] == 0
        assert result["conflicts"] == 0

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_merge_all_single_worktree_success(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(_WT_ONE_AGENT),  # worktree list
            _git_fail(rc=1),  # merge-base --is-ancestor (not merged)
            _git_success("development"),  # rev-parse HEAD
            _git_success(),  # checkout development
            _git_success(),  # merge --no-ff success
            _git_success("development"),  # finally checkout
            _git_success(),  # worktree remove
            _git_success(),  # prune
            _git_success(),  # branch -d
        ]
        result = worktree_merge_all("/Users/shawnwilson/gludd")
        assert result["total"] == 1
        assert result["merged"] == 1
        assert result["conflicts"] == 0

    @patch("general_ludd.git_automation.worktree._run_git")
    def test_merge_all_skips_already_merged(self, mock_run: MagicMock):
        mock_run.side_effect = [
            _git_success(_WT_ONE_AGENT),  # worktree list
            _git_success(),  # merge-base --is-ancestor (already merged)
            _git_success(),  # worktree remove
            _git_success(),  # prune
            _git_success(),  # branch -d
        ]
        result = worktree_merge_all("/Users/shawnwilson/gludd")
        assert result["skipped"] == 1
        assert result["merged"] == 0
        assert result["total"] == 1
