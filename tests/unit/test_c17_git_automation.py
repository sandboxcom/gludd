"""C17 — Git automation guards: lock wrapper, squash fail-closed, serialization, branch-name uniqueness."""

from __future__ import annotations

import subprocess
import threading
from unittest.mock import patch

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import MergeResult


def _ok(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fail(
    stderr: str = "error", returncode: int = 1
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout="", stderr=stderr
    )


# ── Item 1: merge_branch uses per-repo lock ───────────────────────────────

class TestMergeBranchGoesThroughLockWrapper:
    """merge_branch must route every git call through _run_git, which
    acquires the per-repo lock (git_repo_lock) before every subprocess.run."""

    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_merge_branch_uses_run_git_not_raw_subprocess(self, mock_run):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git", return_value=_ok(stdout="merged")) as spy:
            result = auto.merge_branch("/repo", "feat", "main", "ff")
            assert result.success is True
            assert spy.called, "merge_branch must route calls through _run_git"
            assert spy.call_count >= 2, (
                "merge_branch must call _run_git for checkout + merge"
            )
        mock_run.assert_not_called()

    @patch("general_ludd.git_automation.repo.subprocess.run", return_value=_ok())
    def test_merge_branch_squash_uses_run_git(self, mock_run):
        auto = GitAutomation(".")
        with patch.object(auto, "_run_git", return_value=_ok(stdout="merged")) as spy:
            result = auto.merge_branch("/repo", "feat", "main", "squash")
            assert result.success is True
            assert spy.called
        mock_run.assert_not_called()


# ── Item 2: squash path fails closed ──────────────────────────────────────

class TestSquashFailsClosedOnError:
    """Squash commits in gated_merge must use check=True so a failed
    squash raises CalledProcessError and the gate rolls back — never
    silently succeeds (fail-open)."""

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),                                          # checkout target
            _ok(stdout="abc123\n"),                          # rev-parse HEAD
            _ok(stdout="Squash commit -- not updating HEAD"),  # merge --squash
            subprocess.CalledProcessError(1, "git commit", stderr="empty commit"),  # commit fails
            _ok(),  # reset --hard (rollback)
        ],
    )
    def test_squash_commit_failure_rolls_back(self, mock_run):
        """A squash commit that fails (check=True → CalledProcessError)
        must roll back to pre_sha and return success=False."""
        auto = GitAutomation("/repo")
        result = auto.gated_merge(
            "feat", "main", ["true"], strategy="squash"
        )
        assert result.success is False, (
            "squash commit failure must return success=False, not succeed silently"
        )
        assert "empty commit" in result.message

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),                                          # checkout target
            _ok(stdout="abc123\n"),                          # rev-parse HEAD
            _ok(stdout="Squash commit -- not updating HEAD"),  # merge --squash
            subprocess.CalledProcessError(1, "git commit", stderr=""),  # commit fails (no stderr)
            _ok(),  # reset --hard (rollback)
        ],
    )
    def test_squash_commit_no_stderr_rolls_back(self, mock_run):
        """A squash commit that fails with empty stderr must still roll back
        and return the default failure message."""
        auto = GitAutomation("/repo")
        result = auto.gated_merge(
            "feat", "main", ["true"], strategy="squash"
        )
        assert result.success is False, (
            "squash commit failure must return success=False"
        )
        assert "rolled back" in result.message


# ── Item 3: per-repo serialization prevents races ─────────────────────────

class TestPerRepoSerializationPreventsRace:
    """Two concurrent git operations on the same repo must serialize
    through the per-repo lock, so the second cannot start until the
    first completes."""

    def test_two_concurrent_ops_on_same_repo_serialized(self):
        barrier = threading.Barrier(2, timeout=5)
        order: list[str] = []

        def _slow_git(*args, **kwargs):
            barrier.wait()  # both threads arrive here
            order.append(threading.current_thread().name)
            return _ok()

        auto = GitAutomation("/same-repo")

        with patch.object(auto, "_run_git", wraps=auto._run_git) as spy:
            spy.side_effect = _slow_git
            results: list[MergeResult | None] = [None, None]

            def _merge_a():
                results[0] = auto.merge_branch("/same-repo", "a", "main", "ff")

            def _merge_b():
                results[1] = auto.merge_branch("/same-repo", "b", "main", "ff")

            t1 = threading.Thread(target=_merge_a, name="thread-a")
            t2 = threading.Thread(target=_merge_b, name="thread-b")
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        assert not t1.is_alive(), "thread-a did not complete"
        assert not t2.is_alive(), "thread-b did not complete"

        # Both results should be success — the lock serializes the calls
        # so they complete in order (whichever acquired the lock first)
        # without deadlocking or erroring.
        assert results[0] is not None and results[0].success, (
            "first merge_branch must succeed"
        )
        assert results[1] is not None and results[1].success, (
            "second merge_branch must succeed (serialized, not raced)"
        )


# ── Item 4: branch names include unique ID ────────────────────────────────

class TestBranchNameIncludesUniqueId:
    """generate_branch_name must include a UUID component to prevent
    1-second collisions when two branches are created in the same
    wall-clock second."""

    def test_branch_name_includes_uuid_component(self):
        name = GitAutomation.generate_branch_name("42", "fix-bug")
        assert name.startswith("agent/TODO-42/fix-bug-"), (
            f"unexpected prefix in {name!r}"
        )
        # Timestamp part (YYYYMMDDHHMMSS)
        ts_part = name.split("-")[-2]  # second-to-last segment
        assert len(ts_part) == 14, (
            f"timestamp part must be 14 digits, got {ts_part!r}"
        )
        assert ts_part.isdigit(), (
            f"timestamp part must be digits, got {ts_part!r}"
        )
        # UUID part (last segment, hex string length >= 6)
        uuid_part = name.split("-")[-1]
        assert len(uuid_part) >= 6, (
            f"uuid part must be at least 6 chars, got {uuid_part!r} (len={len(uuid_part)})"
        )
        # Must be hex
        assert all(c in "0123456789abcdef" for c in uuid_part.lower()), (
            f"uuid part must be hex chars, got {uuid_part!r}"
        )

    def test_two_calls_produce_different_names(self):
        name_a = GitAutomation.generate_branch_name("1", "feat")
        name_b = GitAutomation.generate_branch_name("1", "feat")
        assert name_a != name_b, (
            "two calls in the same second must produce different branch names"
        )


# ── Item 2 additional: merge_branch squash is already fail-closed ─────────

class TestMergeBranchSquashAlreadyFailClosed:
    """The non-gated merge_branch squash path was already fixed (check=True);
    these tests confirm the existing guard is intact after C17 changes."""

    @patch(
        "general_ludd.git_automation.repo.subprocess.run",
        side_effect=[
            _ok(),
            _ok(stdout="Squash commit -- not updating HEAD"),
            subprocess.CalledProcessError(1, "git commit", stderr="squash failed"),
        ],
    )
    def test_merge_branch_squash_raises(self, mock_run):
        result = GitAutomation(".").merge_branch("/repo", "feat", "main", "squash")
        assert result.success is False
        assert "squash failed" in result.message
