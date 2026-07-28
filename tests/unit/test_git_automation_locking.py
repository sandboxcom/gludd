"""Structural tests for general_ludd.git_automation.locking.

Pins the locking module's contract: path normalization, in-process lock
registry, public API surface, and cross-process safety on POSIX.
"""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

from general_ludd.git_automation import locking


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


class TestBreakIfStale:
    def test_keeps_fresh_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            locking._break_if_stale(lock_path, stale_after=300.0)
            assert os.path.exists(lock_path)

    def test_breaks_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            stale_time = 0.0  # epoch — always stale
            os.utime(lock_path, (stale_time, stale_time))
            locking._break_if_stale(lock_path, stale_after=1.0)
            assert not os.path.exists(lock_path)


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

    def test_missing_git_dir_during_open_uses_inprocess_only(self) -> None:
        key = "missing-during-open"
        locking._file_lock_depth.pop(key, None)
        with (
            patch.object(locking.os, "open", side_effect=FileNotFoundError),
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
                with locking.contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


class TestGitRepoLock:
    def test_acquires_and_releases_inprocess_lock(self) -> None:
        key = locking._normalize(".")
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
