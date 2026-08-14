"""Structural tests for general_ludd.git_automation.locking.

Pins the locking module's contract: path normalization, in-process lock
registry, public API surface, and cross-process safety on POSIX.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import MutableSequence
from multiprocessing.connection import Connection
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.git_automation import locking


def _hold_repo_lock_then_record(
    repo_path: str,
    execution_order: MutableSequence[str],
    name: str,
    hold_secs: float,
) -> None:
    """Spawn-safe worker used to prove cross-process worktree locking."""
    with locking.git_repo_lock(repo_path, timeout=10.0, stale_after=60.0):
        execution_order.append(f"{name}:enter")
        time.sleep(hold_secs)
        execution_order.append(f"{name}:exit")


def _attempt_repo_lock(repo_path: str, result: Connection, timeout: float) -> None:
    """Attempt a repo lock from an importable child-process target."""
    try:
        with locking.git_repo_lock(repo_path, timeout=timeout, stale_after=60.0):
            result.send("acquired")
    except TimeoutError:
        result.send("timed_out")
    finally:
        result.close()


def _crash_holding_repo_lock(repo_path: str) -> None:
    """Exit without context cleanup while holding the kernel mutex."""
    with locking.git_repo_lock(repo_path, timeout=5.0, stale_after=60.0):
        os._exit(73)


def _fork_while_holding_repo_lock(repo_path: str, result: Connection) -> None:
    """Fork from a clean spawned process while the parent owns the mutex."""
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_attempt_repo_lock,
        args=(repo_path, sender, 0.2),
    )
    try:
        with locking.git_repo_lock(repo_path, timeout=1.0, stale_after=60.0):
            process.start()
            sender.close()
            if receiver.poll(5.0):
                result.send(receiver.recv())
            else:
                result.send("no_result")
            process.join(timeout=5.0)
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        receiver.close()
        sender.close()
        process.close()
        result.close()


class TestNormalize:
    def test_realpath_collapses_symlinks_and_dots(self) -> None:
        result = locking._normalize(os.path.abspath("."))
        assert result == os.path.realpath(".")

    def test_different_spellings_same_key(self) -> None:
        a = locking._normalize(".")
        b = locking._normalize(os.path.abspath("."))
        assert a == b

    def test_falls_back_to_abspath_on_oserror(self) -> None:
        with patch("os.path.realpath", side_effect=OSError("boom")):
            result = locking._normalize("/nonexistent/path")
        assert result == os.path.abspath("/nonexistent/path")


class TestGetInprocessLock:
    def test_returns_rlock(self) -> None:
        lock = locking._get_inprocess_lock("test-repo")
        assert isinstance(lock, type(threading.RLock()))

    def test_same_key_returns_same_lock(self) -> None:
        a = locking._get_inprocess_lock("repo-A")
        b = locking._get_inprocess_lock("repo-A")
        assert a is b

    def test_different_keys_return_different_locks(self) -> None:
        a = locking._get_inprocess_lock("repo-A")
        b = locking._get_inprocess_lock("repo-B")
        assert a is not b


class TestGitDir:
    def test_returns_dot_git_when_directory_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            assert locking._git_dir(tmpdir) == git_dir

    def test_returns_none_when_no_dot_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assert locking._git_dir(tmpdir) is None

    def test_returns_none_for_file_not_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_file = os.path.join(tmpdir, ".git")
            with open(git_file, "w") as f:
                f.write("gitdir: /elsewhere\n")
            assert locking._git_dir(tmpdir) is None


class TestGitDirWorktree:
    def test_resolves_worktree_to_common_dir(self) -> None:
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
            Path(main_repo, "empty").touch()
            subprocess.run(
                ["git", "add", "."],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
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
                resolved = locking._git_dir(wt_path)
                assert resolved is not None
                expected = os.path.join(main_repo, ".git")
                assert resolved == expected
                assert os.path.isdir(resolved)
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=main_repo,
                    capture_output=True,
                )

    def test_returns_none_when_rev_parse_fails_in_nongit_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_file = os.path.join(tmpdir, ".git")
            with open(git_file, "w") as f:
                f.write("gitdir: /nonexistent/path\n")
            assert locking._git_dir(tmpdir) is None


class TestBreakIfStale:
    def test_keeps_fresh_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            locking._break_if_stale(lock_path, stale_after=300.0)
            assert os.path.exists(lock_path)

    def test_stale_metadata_never_unlinks_mutex_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            stale_time = 0.0  # epoch — always stale
            os.utime(lock_path, (stale_time, stale_time))
            locking._break_if_stale(lock_path, stale_after=1.0)
            assert os.path.exists(lock_path)


class TestFileLock:
    def test_yields_on_non_posix(self) -> None:
        with (
            patch.object(locking, "_HAVE_FCNTL", False),
            tempfile.TemporaryDirectory() as tmpdir,
            locking._file_lock(tmpdir, "key", timeout=1.0, stale_after=60.0),
        ):
            assert True

    def test_reentrant_nested_acquire_skips_reflock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key = "test-reentrant"
            locking._file_lock_depth.pop(key, None)
            with locking._file_lock(tmpdir, key, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_depth.get(key) == 1
                with locking._file_lock(tmpdir, key, timeout=5.0, stale_after=60.0):
                    assert locking._file_lock_depth.get(key) == 2
            assert locking._file_lock_depth.get(key, 0) == 0

    def test_reentrant_owner_is_current_process_and_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key = "test-owner"
            with locking._file_lock(tmpdir, key, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_owner[key] == (
                    os.getpid(),
                    threading.get_ident(),
                )
            assert key not in locking._file_lock_owner

    def test_missing_git_dir_during_open_uses_inprocess_only(self) -> None:
        key = "missing-during-open"
        locking._file_lock_depth.pop(key, None)
        with (
            patch("general_ludd.git_automation.locking.os.open", side_effect=FileNotFoundError),
            locking._file_lock("/tmp/gludd-missing-git-dir", key, timeout=1.0, stale_after=60.0),
        ):
            assert locking._file_lock_depth.get(key, 0) == 0

    def test_times_out_on_contended_lock(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)

            lock_path = os.path.join(git_dir, locking._LOCK_FILENAME)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                key = "timeout-test"
                locking._file_lock_depth.pop(key, None)
                with (
                    pytest.raises(TimeoutError, match="timed out"),
                    locking._file_lock(git_dir, key, timeout=0.1, stale_after=300.0),
                ):
                    pass
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


class TestGitRepoLock:
    def test_acquires_and_releases_inprocess_lock(self) -> None:
        git_dir = locking._git_dir(".")
        key = locking._normalize(git_dir) if git_dir is not None else locking._normalize(".")
        with locking.git_repo_lock(".", timeout=1.0, stale_after=60.0):
            rlock = locking._repo_locks.get(key)
            assert rlock is not None
            acquired = rlock.acquire(blocking=False)
            if acquired:
                rlock.release()

    def test_context_manager_contract(self) -> None:
        with locking.git_repo_lock(".", timeout=1.0, stale_after=60.0):
            pass

    def test_no_dot_git_directory_uses_inprocess_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
            key = locking._normalize(tmpdir)
            assert key in locking._repo_locks


class TestGitRepoLockWorktree:
    def test_git_repo_lock_uses_common_dir_inside_worktree(self) -> None:
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
                with locking.git_repo_lock(wt_path, timeout=1.0, stale_after=60.0):
                    common_key = locking._normalize(os.path.join(main_repo, ".git"))
                    assert common_key in locking._repo_locks
                    wt_key = locking._normalize(wt_path)
                    main_key = locking._normalize(main_repo)
                    assert wt_key not in locking._repo_locks
                    if wt_key != common_key:
                        assert main_key not in locking._repo_locks
                    rlock = locking._repo_locks[common_key]
                    assert isinstance(rlock, type(threading.RLock()))
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=main_repo,
                    capture_output=True,
                )

    def test_git_repo_lock_serializes_concurrent_worktree_processes(self) -> None:
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
            wt_path = os.path.join(tmpdir, "wt")
            subprocess.run(
                ["git", "worktree", "add", wt_path],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            try:
                with multiprocessing.Manager() as manager:
                    execution_order = manager.list()
                    p1 = multiprocessing.Process(
                        target=_hold_repo_lock_then_record,
                        args=(wt_path, execution_order, "first", 0.3),
                    )
                    p2 = multiprocessing.Process(
                        target=_hold_repo_lock_then_record,
                        args=(wt_path, execution_order, "second", 0.1),
                    )

                    p1.start()
                    time.sleep(0.05)
                    p2.start()

                    p1.join(timeout=10)
                    p2.join(timeout=10)

                    assert p1.exitcode == 0
                    assert p2.exitcode == 0
                    events = list(execution_order)
                    assert sorted(events) == [
                        "first:enter",
                        "first:exit",
                        "second:enter",
                        "second:exit",
                    ]
                    for enter, leave in zip(events[::2], events[1::2], strict=True):
                        assert enter.endswith(":enter")
                        assert leave == enter.replace(":enter", ":exit")
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=main_repo,
                    capture_output=True,
                )

    @pytest.mark.skipif(
        "fork" not in multiprocessing.get_all_start_methods(),
        reason="fork start method is unavailable",
    )
    def test_forked_child_does_not_inherit_reentrant_ownership(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            context = multiprocessing.get_context("spawn")
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(
                target=_fork_while_holding_repo_lock,
                args=(tmpdir, sender),
            )
            try:
                process.start()
                sender.close()
                assert receiver.poll(10.0), "child did not report its lock outcome"
                assert receiver.recv() == "timed_out"
                process.join(timeout=10.0)
                assert process.exitcode == 0
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5.0)
                receiver.close()
                sender.close()
                process.close()

    def test_spawned_child_observes_timeout_while_parent_owns_lock(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            context = multiprocessing.get_context("spawn")
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(
                target=_attempt_repo_lock,
                args=(tmpdir, sender, 0.2),
            )
            try:
                with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                    process.start()
                    sender.close()
                    assert receiver.poll(10.0), "child did not report its lock outcome"
                    assert receiver.recv() == "timed_out"
                    process.join(timeout=10.0)
                    assert process.exitcode == 0
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5.0)
                receiver.close()
                sender.close()
                process.close()

    def test_crashed_spawned_owner_releases_kernel_mutex(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            context = multiprocessing.get_context("spawn")
            process = context.Process(target=_crash_holding_repo_lock, args=(tmpdir,))
            try:
                process.start()
                process.join(timeout=10.0)
                assert process.exitcode == 73
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5.0)
                process.close()

            with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                pass


class TestAsyncGitRepoLock:
    @pytest.mark.asyncio
    async def test_returns_context_manager(self) -> None:
        cm = await locking.async_git_repo_lock(".", timeout=1.0, stale_after=60.0)
        try:
            assert hasattr(cm, "__enter__")
            assert hasattr(cm, "__exit__")
        finally:
            cm.__exit__(None, None, None)

    @pytest.mark.asyncio
    async def test_context_manager_releases_on_executor_thread(self) -> None:
        cm = await locking.async_git_repo_lock(".", timeout=1.0, stale_after=60.0)
        with cm:
            pass


class TestModuleExports:
    def test_git_repo_lock_is_callable(self) -> None:
        assert callable(locking.git_repo_lock)

    def test_async_git_repo_lock_is_callable(self) -> None:
        assert callable(locking.async_git_repo_lock)

    def test_default_constants_are_positive(self) -> None:
        assert locking._DEFAULT_ACQUIRE_TIMEOUT > 0
        assert locking._DEFAULT_STALE_AFTER > 0
        assert locking._POLL_INTERVAL > 0
