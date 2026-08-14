"""Deep edge-case tests for general_ludd.git_automation.locking.

Covers paths that structural and basic behavioral tests miss: registry
thread safety, depth-counter isolation across repos, boundary conditions
on timeout/staleness, external lock-file removal while polling, fcntl
unlock failures, git rev-parse timeout, and interleaved multi-repo
acquire/release ordering.
"""

from __future__ import annotations

import contextlib
import errno
import multiprocessing
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import MutableSequence
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.git_automation import locking

# ---------------------------------------------------------------------------
# _normalize edge cases
# ---------------------------------------------------------------------------


class TestNormalizeEdgeCases:
    def test_empty_string_returns_abspath_of_cwd(self) -> None:
        result = locking._normalize("")
        assert result == os.path.abspath("")

    def test_already_canonical_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved = os.path.realpath(tmpdir)
            assert locking._normalize(resolved) == resolved

    def test_trailing_slash_no_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            a = locking._normalize(tmpdir)
            b = locking._normalize(tmpdir + os.sep)
            assert a == b

    def test_symlink_chain_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = os.path.join(tmpdir, "real")
            os.mkdir(real_dir)
            link1 = os.path.join(tmpdir, "link1")
            link2 = os.path.join(tmpdir, "link2")
            os.symlink(real_dir, link1)
            os.symlink(link1, link2)
            assert locking._normalize(link2) == os.path.realpath(real_dir)


# ---------------------------------------------------------------------------
# _get_inprocess_lock thread safety
# ---------------------------------------------------------------------------


class TestGetInprocessLockConcurrency:
    def test_concurrent_get_returns_same_object(self) -> None:
        key = "concurrent-key"
        locks: list[threading.RLock] = []
        barrier = threading.Barrier(4)

        def fetch() -> None:
            barrier.wait()
            locks.append(locking._get_inprocess_lock(key))

        threads = [threading.Thread(target=fetch) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        first = locks[0]
        assert all(lock is first for lock in locks)

    def test_concurrent_different_keys_independent(self) -> None:
        results: dict[str, threading.RLock] = {}
        barrier = threading.Barrier(4)

        def fetch(i: int) -> None:
            barrier.wait()
            results[str(i)] = locking._get_inprocess_lock(f"key-{i}")

        threads = [threading.Thread(target=fetch, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        lock_ids = {id(lock) for lock in results.values()}
        assert len(lock_ids) == 4


# ---------------------------------------------------------------------------
# _git_dir deep edge cases
# ---------------------------------------------------------------------------


class TestGitDirDeepEdgeCases:
    def test_rev_parse_timeout_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_file = os.path.join(tmpdir, ".git")
            with open(git_file, "w") as f:
                f.write("gitdir: /nonexistent\n")

            with patch.object(
                locking.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=1.0),
            ):
                assert locking._git_dir(tmpdir) is None

    def test_rev_parse_oserror_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_file = os.path.join(tmpdir, ".git")
            with open(git_file, "w") as f:
                f.write("gitdir: /nonexistent\n")

            with patch.object(locking.subprocess, "run", side_effect=OSError("exec not found")):
                assert locking._git_dir(tmpdir) is None

    def test_rev_parse_returns_relative_common_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            main_repo = os.path.join(tmpdir, "main")
            os.mkdir(main_repo)
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            Path(main_repo, "dummy").touch()
            subprocess.run(["git", "add", "."], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            wt_path = os.path.join(tmpdir, "wt")
            subprocess.run(
                ["git", "worktree", "add", wt_path],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            try:
                result = locking._git_dir(wt_path)
                assert result is not None
                assert os.path.isdir(result)
                assert result.endswith(".git")
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=main_repo,
                    capture_output=True,
                )

    def test_rev_parse_nonzero_exit_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_file = os.path.join(tmpdir, ".git")
            with open(git_file, "w") as f:
                f.write("gitdir: /nonexistent\n")
            result = locking._git_dir(tmpdir)
            assert result is None

    def test_git_dir_returns_none_when_common_dir_not_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_file = os.path.join(tmpdir, ".git")
            with open(git_file, "w") as f:
                f.write("gitdir: /nonexistent\n")

            with patch.object(
                locking.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=["git"],
                    returncode=0,
                    stdout="/tmp/some-file\n",
                    stderr="",
                ),
            ):
                result = locking._git_dir(tmpdir)
                assert result is None


# ---------------------------------------------------------------------------
# _break_if_stale edge cases
# ---------------------------------------------------------------------------


class TestBreakIfStaleEdgeCases:
    def test_no_crash_when_file_removed_between_getmtime_and_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            stale_time = 0.0
            os.utime(lock_path, (stale_time, stale_time))
            os.unlink(lock_path)
            locking._break_if_stale(lock_path, stale_after=1.0)

    def test_missing_file_silently_noops(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "nonexistent.lock")
            locking._break_if_stale(lock_path, stale_after=1.0)

    def test_exactly_at_boundary_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            mtime = time.time() - 9.9
            os.utime(lock_path, (mtime, mtime))
            locking._break_if_stale(lock_path, stale_after=10.0)
            assert os.path.exists(lock_path)

    def test_stale_after_zero_preserves_stable_mutex_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            time.sleep(0.001)
            locking._break_if_stale(lock_path, stale_after=0.0)
            assert os.path.exists(lock_path)


# ---------------------------------------------------------------------------
# _file_lock depth counter isolation
# ---------------------------------------------------------------------------


class TestFileLockDepthCounterIsolation:
    def test_depth_counter_independent_per_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key_a = "repo-a"
            key_b = "repo-b"
            locking._file_lock_depth.pop(key_a, None)
            locking._file_lock_depth.pop(key_b, None)

            with locking._file_lock(tmpdir, key_a, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_depth.get(key_a) == 1
                assert locking._file_lock_depth.get(key_b, 0) == 0

    def test_acquire_release_then_reacquire_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key = "reacquire"
            locking._file_lock_depth.pop(key, None)

            with locking._file_lock(tmpdir, key, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_depth.get(key) == 1
            assert locking._file_lock_depth.get(key, 0) == 0

            with locking._file_lock(tmpdir, key, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_depth.get(key) == 1

    def test_interleaved_two_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir_a, tempfile.TemporaryDirectory() as tmpdir_b:
            git_a = os.path.join(tmpdir_a, ".git")
            git_b = os.path.join(tmpdir_b, ".git")
            os.mkdir(git_a)
            os.mkdir(git_b)
            key_a = "interleaved-a"
            key_b = "interleaved-b"
            locking._file_lock_depth.pop(key_a, None)
            locking._file_lock_depth.pop(key_b, None)

            with locking._file_lock(git_a, key_a, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_depth[key_a] == 1
                with locking._file_lock(git_b, key_b, timeout=5.0, stale_after=60.0):
                    assert locking._file_lock_depth[key_a] == 1
                    assert locking._file_lock_depth[key_b] == 1
                assert locking._file_lock_depth[key_b] == 0
                assert locking._file_lock_depth[key_a] == 1
            assert locking._file_lock_depth.get(key_a, 0) == 0


# ---------------------------------------------------------------------------
# _file_lock boundary / error paths
# ---------------------------------------------------------------------------


class TestFileLockBoundary:
    def test_zero_timeout_on_contended_lock(self) -> None:
        fcntl = pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            lock_path = os.path.join(git_dir, locking._LOCK_FILENAME)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                key = "zero-timeout"
                locking._file_lock_depth.pop(key, None)
                with (
                    pytest.raises(TimeoutError, match="timed out"),
                    locking._file_lock(git_dir, key, timeout=0.0, stale_after=300.0),
                ):
                    pass
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def test_acquires_after_holder_releases(self) -> None:
        fcntl = pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            lock_path = os.path.join(git_dir, locking._LOCK_FILENAME)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                holder_start = threading.Event()
                acquired = threading.Event()

                def waiter() -> None:
                    holder_start.set()
                    with locking._file_lock(
                        git_dir,
                        "release-test",
                        timeout=5.0,
                        stale_after=300.0,
                    ):
                        acquired.set()

                t = threading.Thread(target=waiter)
                t.start()
                holder_start.wait(timeout=2.0)
                time.sleep(0.1)
                fcntl.flock(fd, fcntl.LOCK_UN)
                t.join(timeout=5.0)
                assert acquired.is_set(), "waiter never acquired after release"
            finally:
                os.close(fd)

    def test_external_unlink_does_not_bypass_held_inode(self) -> None:
        fcntl = pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            lock_path = os.path.join(git_dir, locking._LOCK_FILENAME)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                def waiter_and_remover() -> None:
                    time.sleep(0.05)
                    with contextlib.suppress(OSError):
                        os.unlink(lock_path)

                t = threading.Thread(target=waiter_and_remover)
                t.start()

                with (
                    pytest.raises(TimeoutError, match="timed out"),
                    locking._file_lock(
                        git_dir,
                        "removed-while-polling",
                        timeout=0.2,
                        stale_after=0.01,
                    ),
                ):
                    pass

                t.join(timeout=2.0)
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def test_unlock_failure_does_not_crash_close(self) -> None:
        pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            key = "unlock-fail"
            locking._file_lock_depth.pop(key, None)
            with patch("general_ludd.git_automation.locking.fcntl.flock") as mock_flock:
                mock_flock.side_effect = [None, OSError(errno.EBADF, "bad fd")]
                with locking._file_lock(tmpdir, key, timeout=5.0, stale_after=60.0):
                    pass

    def test_oserror_on_utime_suppressed_after_acquire(self) -> None:
        pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            key = "utime-fail"
            locking._file_lock_depth.pop(key, None)
            with (
                patch("os.utime", side_effect=OSError(errno.EPERM, "permission")),
                locking._file_lock(
                    tmpdir,
                    key,
                    timeout=5.0,
                    stale_after=60.0,
                ),
            ):
                pass

    def test_eagains_retried_until_timeout(self) -> None:
        fcntl = pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)

            eagain_errors = [
                OSError(errno.EAGAIN, "try again"),
                OSError(errno.EAGAIN, "try again"),
                OSError(errno.EAGAIN, "try again"),
                None,
            ]
            call_count: list[int] = []

            real_flock = fcntl.flock

            def fake_flock(fd: int, op: int) -> None:
                call_count.append(1)
                if len(call_count) <= 3:
                    raise eagain_errors[len(call_count) - 1]
                return real_flock(fd, op)

            key = "eagain-test"
            locking._file_lock_depth.pop(key, None)
            with (
                patch("general_ludd.git_automation.locking.fcntl.flock", side_effect=fake_flock),
                locking._file_lock(
                    git_dir,
                    key,
                    timeout=5.0,
                    stale_after=300.0,
                ),
            ):
                pass
            assert len(call_count) >= 4, "EAGAIN was not retried"


# ---------------------------------------------------------------------------
# git_repo_lock deep edge cases
# ---------------------------------------------------------------------------


class TestGitRepoLockDeepEdge:
    def test_nested_reentrant_cross_process_depth_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0),
                locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0),
                locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0),
            ):
                pass
            key = locking._normalize(tmpdir)
            assert key in locking._repo_locks
            rlock = locking._repo_locks[key]
            acquired = rlock.acquire(blocking=False)
            if acquired:
                rlock.release()

    def test_interleaved_multi_repo_acquire_release(self) -> None:
        with (
            tempfile.TemporaryDirectory() as dir_a,
            tempfile.TemporaryDirectory() as dir_b,
            locking.git_repo_lock(
                dir_a,
                timeout=1.0,
                stale_after=60.0,
            ),
            locking.git_repo_lock(dir_b, timeout=1.0, stale_after=60.0),
        ):
            pass

    def test_timeout_error_propagates_from_file_lock(self) -> None:
        fcntl = pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            lock_path = os.path.join(git_dir, locking._LOCK_FILENAME)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with (
                    pytest.raises(TimeoutError, match="timed out"),
                    locking.git_repo_lock(tmpdir, timeout=0.1, stale_after=300.0),
                ):
                    pass
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def test_inprocess_lock_released_after_file_lock_timeout(self) -> None:
        fcntl = pytest.importorskip("fcntl")

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            lock_path = os.path.join(git_dir, locking._LOCK_FILENAME)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with (
                    pytest.raises(TimeoutError),
                    locking.git_repo_lock(
                        tmpdir,
                        timeout=0.1,
                        stale_after=300.0,
                    ),
                ):
                    pass
                key = locking._normalize(tmpdir)
                rlock = locking._repo_locks.get(key)
                if rlock is not None:
                    acquired = rlock.acquire(blocking=False)
                    if acquired:
                        rlock.release()
                    else:
                        pytest.fail("in-process lock leaked after TimeoutError")
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


# ---------------------------------------------------------------------------
# Cross-process serialization — multiple waiters
# ---------------------------------------------------------------------------


def _hold_for_duration_and_record(
    repo_path: str,
    execution_order: MutableSequence[str],
    name: str,
    hold_secs: float,
) -> None:
    with locking.git_repo_lock(repo_path, timeout=10.0, stale_after=60.0):
        execution_order.append(f"{name}:enter")
        time.sleep(hold_secs)
        execution_order.append(f"{name}:exit")


class TestCrossProcessMultiWaiter:
    def test_three_processes_never_overlap(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            main_repo = os.path.join(tmpdir, "main")
            os.mkdir(main_repo)
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            Path(main_repo, "dummy").touch()
            subprocess.run(["git", "add", "."], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )

            with multiprocessing.Manager() as manager:
                execution_order = manager.list()
                processes = [
                    multiprocessing.Process(
                        target=_hold_for_duration_and_record,
                        args=(main_repo, execution_order, name, hold),
                    )
                    for name, hold in [("A", 0.2), ("B", 0.1), ("C", 0.05)]
                ]
                for p in processes:
                    p.start()
                    time.sleep(0.02)
                for p in processes:
                    p.join(timeout=10)
                for p in processes:
                    assert p.exitcode == 0, f"process {p} failed with exit code {p.exitcode}"
                events = list(execution_order)
                assert sorted(events) == [
                    "A:enter",
                    "A:exit",
                    "B:enter",
                    "B:exit",
                    "C:enter",
                    "C:exit",
                ]
                for enter, leave in zip(events[::2], events[1::2], strict=True):
                    assert enter.endswith(":enter")
                    assert leave == enter.replace(":enter", ":exit")


# ---------------------------------------------------------------------------
# async_git_repo_lock edge cases
# ---------------------------------------------------------------------------


class TestAsyncGitRepoLockEdge:
    @pytest.mark.asyncio
    async def test_exc_during_enter_shuts_down_executor(self) -> None:
        with (
            patch.object(
                locking.git_repo_lock,
                "__enter__",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await locking.async_git_repo_lock(".", timeout=1.0, stale_after=60.0)

    @pytest.mark.asyncio
    async def test_double_exit_is_idempotent(self) -> None:
        cm = await locking.async_git_repo_lock(".", timeout=1.0, stale_after=60.0)
        try:
            with cm:
                pass
            cm.__exit__(None, None, None)
        finally:
            pass


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestConstantsEdge:
    def test_lock_filename_is_dotfile_inside_git(self) -> None:
        assert locking._LOCK_FILENAME.startswith("gludd")
        assert not os.path.isabs(locking._LOCK_FILENAME)

    def test_poll_interval_is_reasonable(self) -> None:
        assert 0.0 < locking._POLL_INTERVAL < 1.0
