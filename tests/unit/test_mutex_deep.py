"""Deep mutex/lock correctness tests for general_ludd.git_automation.locking.

Probes internal invariants: reentrancy depth tracking, stale lock liveness,
timeout behavior under contention, thread safety of the registry, release
cleanup, exclusion guarantees, and cross-process serialization.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import tempfile
import threading
import time
from collections.abc import MutableSequence
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.git_automation import locking

# ── cross-process helper ───────────────────────────────────────────────────


def _hold_repo_lock_then_record(
    repo_path: str,
    execution_order: MutableSequence[str],
    name: str,
    hold_secs: float,
) -> None:
    with locking.git_repo_lock(repo_path, timeout=10.0, stale_after=60.0):
        execution_order.append(f"{name}:enter")
        time.sleep(hold_secs)
        execution_order.append(f"{name}:exit")


def _acquire_and_signal(
    repo_path: str,
    acquired: threading.Event,
    order: list[int],
    idx: int,
) -> None:
    with locking.git_repo_lock(repo_path, timeout=10.0, stale_after=60.0):
        acquired.set()
        order.append(idx)
        time.sleep(0.05)


# ── Lock Acquisition ───────────────────────────────────────────────────────


class TestLockAcquisition:
    def test_context_manager_acquires_and_holds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key = locking._normalize(tmpdir)
            with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                rlock = locking._repo_locks.get(key)
                assert rlock is not None
                # RLock is re-entrant for the owner; verify it is held
                # from a different thread where it must block.
                blocked: list[bool] = []

                def probe() -> None:
                    blocked.append(not rlock.acquire(blocking=False))
                    if not blocked[-1]:
                        rlock.release()

                t = threading.Thread(target=probe)
                t.start()
                t.join(timeout=2)
                assert blocked == [True]

    def test_acquire_populates_registry_for_new_path(self) -> None:
        test_key = "__test_acquire_registry__"
        locking._repo_locks.pop(test_key, None)
        locking._file_lock_depth.pop(test_key, None)
        with tempfile.TemporaryDirectory() as tmpdir, locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
            nkey = locking._normalize(tmpdir)
            assert nkey in locking._repo_locks

    def test_acquire_locks_file_lock_when_git_dir_exists(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            key = locking._normalize(git_dir)
            locking._file_lock_depth.pop(key, None)
            with locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_depth.get(key, 0) > 0

    def test_acquire_with_no_dot_git_uses_inprocess_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key = locking._normalize(tmpdir)
            with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                assert key in locking._repo_locks
                assert locking._file_lock_depth.get(key, 0) == 0


# ── Lock Release ───────────────────────────────────────────────────────────


class TestLockRelease:
    def test_release_frees_inprocess_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key = locking._normalize(tmpdir)
            with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                pass
            rlock = locking._repo_locks.get(key)
            assert rlock is not None
            assert rlock.acquire(blocking=False)
            rlock.release()

    def test_release_resets_file_lock_depth_to_zero(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            key = locking._normalize(git_dir)
            locking._file_lock_depth.pop(key, None)
            with locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0):
                pass
            assert locking._file_lock_depth.get(key, 0) == 0

    def test_release_allows_subsequent_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                pass
            with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                pass

    def test_exception_during_hold_still_releases_inprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key = locking._normalize(tmpdir)
            try:
                with locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0):
                    raise ValueError("boom")
            except ValueError:
                pass
            rlock = locking._repo_locks.get(key)
            assert rlock is not None
            assert rlock.acquire(blocking=False)
            rlock.release()


# ── Deadlock Detection / Self-Deadlock Prevention ──────────────────────────


class TestDeadlockDetection:
    def test_nested_acquire_same_thread_does_not_deadlock(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0),
            locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0),
            locking.git_repo_lock(tmpdir, timeout=1.0, stale_after=60.0),
        ):
            pass

    def test_triple_nested_depth_nonzero(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            key = locking._normalize(git_dir)
            locking._file_lock_depth.pop(key, None)
            with (
                locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0),
                locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0),
                locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0),
            ):
                assert locking._file_lock_depth.get(key, 0) > 0

    def test_stale_lock_metadata_preserves_mutex_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            os.utime(lock_path, (0.0, 0.0))
            locking._break_if_stale(lock_path, stale_after=1.0)
            assert os.path.exists(lock_path)

    def test_fresh_lock_not_broken(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "test.lock")
            with open(lock_path, "w") as f:
                f.write("")
            locking._break_if_stale(lock_path, stale_after=300.0)
            assert os.path.exists(lock_path)

    def test_missing_lock_file_not_broken(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "nonexistent.lock")
            locking._break_if_stale(lock_path, stale_after=1.0)
            assert not os.path.exists(lock_path)

    def test_oserror_on_getmtime_is_silent(self) -> None:
        locking._break_if_stale("/nonexistent/path/lock.lock", stale_after=1.0)


# ── Reentrancy ─────────────────────────────────────────────────────────────


class TestReentrancy:
    def test_rlock_is_truly_reentrant(self) -> None:
        rlock = threading.RLock()
        rlock.acquire()
        try:
            rlock.acquire()
            try:
                rlock.acquire()
            finally:
                rlock.release()
        finally:
            rlock.release()
        rlock.release()

    def test_file_lock_depth_increments_on_nested_acquire(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            key = "depth-increment-test"
            locking._file_lock_depth.pop(key, None)
            with locking._file_lock(git_dir, key, timeout=5.0, stale_after=60.0):
                assert locking._file_lock_depth.get(key) == 1
                with locking._file_lock(git_dir, key, timeout=5.0, stale_after=60.0):
                    assert locking._file_lock_depth.get(key) == 2
                assert locking._file_lock_depth.get(key) == 1
            assert locking._file_lock_depth.get(key, 0) == 0

    def test_reentrancy_across_both_layers(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            key = locking._normalize(git_dir)
            locking._file_lock_depth.pop(key, None)
            with (
                locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0),
                locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0),
            ):
                rlock = locking._repo_locks.get(key)
                assert rlock is not None
                # Verify held from a different thread
                blocked: list[bool] = []

                def probe() -> None:
                    blocked.append(not rlock.acquire(blocking=False))
                    if not blocked[-1]:
                        rlock.release()

                t = threading.Thread(target=probe)
                t.start()
                t.join(timeout=2)
                assert blocked == [True]

    def test_get_inprocess_lock_same_key_returns_same_object(self) -> None:
        a = locking._get_inprocess_lock("reentrant-key-A")
        b = locking._get_inprocess_lock("reentrant-key-A")
        assert a is b

    def test_get_inprocess_lock_different_keys_different_objects(self) -> None:
        a = locking._get_inprocess_lock("reentrant-key-X")
        b = locking._get_inprocess_lock("reentrant-key-Y")
        assert a is not b


# ── Timeout ────────────────────────────────────────────────────────────────


class TestTimeout:
    def test_times_out_on_contended_file_lock(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            import fcntl

            lock_path = os.path.join(git_dir, locking._LOCK_FILENAME)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                key = "timeout-contended"
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

    def test_poll_interval_is_positive(self) -> None:
        assert locking._POLL_INTERVAL > 0

    def test_default_timeout_is_positive(self) -> None:
        assert locking._DEFAULT_ACQUIRE_TIMEOUT > 0

    def test_no_timeout_when_uncontended(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            with locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0):
                pass

    def test_stale_after_exceeds_acquire_timeout(self) -> None:
        assert locking._DEFAULT_STALE_AFTER > locking._DEFAULT_ACQUIRE_TIMEOUT


# ── Priority Inversion ─────────────────────────────────────────────────────


class TestPriorityInversion:
    def test_holder_completes_before_contender_acquires(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            order: list[int] = []
            holder_started = threading.Event()

            def holder() -> None:
                with locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0):
                    order.append(1)
                    holder_started.set()
                    time.sleep(0.3)
                    order.append(2)
                order.append(3)

            def contender() -> None:
                holder_started.wait()
                time.sleep(0.05)
                with locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0):
                    order.append(4)

            t1 = threading.Thread(target=holder)
            t2 = threading.Thread(target=contender)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            assert order.index(2) < order.index(4), f"order={order}"

    def test_lock_exclusion_guarantee(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            shared: list[int] = []
            errors: list[str] = []

            def worker(idx: int) -> None:
                with locking.git_repo_lock(tmpdir, timeout=10.0, stale_after=60.0):
                    shared.append(idx)
                    time.sleep(0.05)
                    if len(shared) > 1:
                        errors.append(f"concurrent by {idx}: shared={shared[:]}")
                    shared.pop()

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            assert errors == []

    def test_high_frequency_acquires_never_drop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            results: list[int] = []

            def worker(i: int) -> None:
                with locking.git_repo_lock(tmpdir, timeout=10.0, stale_after=60.0):
                    results.append(i)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert len(results) == 10
            assert sorted(results) == list(range(10))


# ── Fair Queue ─────────────────────────────────────────────────────────────


class TestFairQueue:
    def test_serializes_concurrent_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            order: list[int] = []
            threads = [
                threading.Thread(target=_acquire_and_signal, args=(tmpdir, threading.Event(), order, i))
                for i in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert sorted(order) == list(range(5))
            assert len(order) == 5

    def test_lock_available_immediately_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            released = threading.Event()

            def holder() -> None:
                with locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0):
                    time.sleep(0.2)
                released.set()

            def waiter() -> None:
                released.wait()
                with locking.git_repo_lock(tmpdir, timeout=5.0, stale_after=60.0):
                    pass

            t1 = threading.Thread(target=holder)
            t2 = threading.Thread(target=waiter)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            assert not t1.is_alive()
            assert not t2.is_alive()

    def test_registry_guard_is_thread_safe(self) -> None:
        keys: list[str] = []

        def add_key(i: int) -> None:
            key = f"thread-safety-{i}"
            locking._get_inprocess_lock(key)
            keys.append(key)

        threads = [threading.Thread(target=add_key, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(keys) == 20
        for i in range(20):
            assert f"thread-safety-{i}" in locking._repo_locks

    def test_file_lock_depth_unwinds_correctly(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.mkdir(git_dir)
            key = "depth-unwind-test"
            locking._file_lock_depth.pop(key, None)
            with locking._file_lock(git_dir, key, timeout=5.0, stale_after=60.0):
                for _ in range(5):
                    with locking._file_lock(git_dir, key, timeout=5.0, stale_after=60.0):
                        pass
            assert locking._file_lock_depth.get(key, 0) == 0


# ── Cross-Process Serialization ────────────────────────────────────────────


class TestCrossProcess:
    def test_worktree_lock_serializes_independent_processes(self) -> None:
        if not locking._HAVE_FCNTL:
            pytest.skip("fcntl not available")
        import subprocess as sp

        with tempfile.TemporaryDirectory() as tmpdir:
            main_repo = os.path.join(tmpdir, "main")
            os.mkdir(main_repo)
            sp.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            sp.run(
                ["git", "config", "user.email", "t@t"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            sp.run(
                ["git", "config", "user.name", "T"],
                cwd=main_repo,
                check=True,
                capture_output=True,
            )
            Path(main_repo, "dummy").touch()
            sp.run(["git", "add", "."], cwd=main_repo, check=True, capture_output=True)
            sp.run(["git", "commit", "-m", "i"], cwd=main_repo, check=True, capture_output=True)
            wt_path = os.path.join(tmpdir, "wt")
            sp.run(
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
                sp.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=main_repo,
                    capture_output=True,
                )


# ── Registry / Normalize ───────────────────────────────────────────────────


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


# ── Module Invariants ──────────────────────────────────────────────────────


class TestModuleInvariants:
    def test_constants_are_positive(self) -> None:
        assert locking._DEFAULT_ACQUIRE_TIMEOUT > 0
        assert locking._DEFAULT_STALE_AFTER > 0
        assert locking._POLL_INTERVAL > 0
        assert locking._LOCK_FILENAME == "gludd-git.lock"

    def test_registry_guard_is_lock(self) -> None:
        assert isinstance(locking._registry_guard, type(threading.Lock()))

    def test_repo_locks_is_dict(self) -> None:
        assert isinstance(locking._repo_locks, dict)

    def test_file_lock_depth_is_dict(self) -> None:
        assert isinstance(locking._file_lock_depth, dict)

    def test_git_repo_lock_is_callable(self) -> None:
        assert callable(locking.git_repo_lock)

    def test_async_git_repo_lock_is_callable(self) -> None:
        assert callable(locking.async_git_repo_lock)
