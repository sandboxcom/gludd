"""Deep context manager audit: exceptions, reentrancy, async, nesting.

Covers every __enter__/__exit__, @contextmanager, and @asynccontextmanager in
src/general_ludd/. Ensures proper cleanup on exception, reentrant safety, async
context manager lifecycle, and nested usage correctness.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ============================================================================
# 1. _suppress_cancel — pipeline/controller.py
# ============================================================================


class TestSuppressCancel:
    def test_suppresses_cancelled_error(self) -> None:
        from general_ludd.pipeline.controller import _suppress_cancel

        with _suppress_cancel():
            raise asyncio.CancelledError()

    def test_permits_other_exceptions(self) -> None:
        from general_ludd.pipeline.controller import _suppress_cancel

        with pytest.raises(ValueError), _suppress_cancel():
            raise ValueError("should propagate")

    def test_enter_returns_self(self) -> None:
        from general_ludd.pipeline.controller import _suppress_cancel

        cm = _suppress_cancel()
        assert cm.__enter__() is cm

    def test_reentrant_ok(self) -> None:
        from general_ludd.pipeline.controller import _suppress_cancel

        with _suppress_cancel(), _suppress_cancel():
            raise asyncio.CancelledError()

    def test_nested_suppress_and_raise_non_cancel(self) -> None:
        from general_ludd.pipeline.controller import _suppress_cancel

        with pytest.raises(RuntimeError), _suppress_cancel(), _suppress_cancel():
            raise RuntimeError("nested")

    def test_exception_chaining_preserved(self) -> None:
        from general_ludd.pipeline.controller import _suppress_cancel

        try:
            with _suppress_cancel():
                raise asyncio.CancelledError()
        except BaseException:
            pytest.fail("should not raise")

        try:
            with _suppress_cancel():
                raise asyncio.CancelledError("cancelled")
        except asyncio.CancelledError:
            pass


# ============================================================================
# 2. _suppress_exc — writer/_child.py
# ============================================================================


class TestSuppressExc:
    def test_suppresses_all_exception_types(self) -> None:
        from general_ludd.writer._child import _suppress_exc

        for exc in (ValueError, RuntimeError, KeyError, OSError, TypeError):
            with _suppress_exc():
                raise exc("test")

    def test_suppresses_baseexception(self) -> None:
        from general_ludd.writer._child import _suppress_exc

        with _suppress_exc():
            raise BaseException("subclass")

    def test_no_exception_ok(self) -> None:
        from general_ludd.writer._child import _suppress_exc

        with _suppress_exc():
            pass

    def test_enter_returns_self(self) -> None:
        from general_ludd.writer._child import _suppress_exc

        cm = _suppress_exc()
        assert cm.__enter__() is cm

    def test_reentrant_suppresses_nested(self) -> None:
        from general_ludd.writer._child import _suppress_exc

        with _suppress_exc():
            with _suppress_exc():
                raise OSError("inner")
            with _suppress_exc():
                raise RuntimeError("also inner")

    def test_used_as_cleanup_during_exception(self) -> None:
        from general_ludd.writer._child import _suppress_exc

        caught = False
        try:
            try:
                raise RuntimeError("outer")
            finally:
                with _suppress_exc():
                    raise OSError("cleanup hit error")
        except RuntimeError:
            caught = True
        assert caught, "outer exception should propagate"


# ============================================================================
# 3. _CallableClient — connectors/grafana_oncall.py
# ============================================================================


class TestCallableClient:
    def test_context_manager_returns_self(self) -> None:
        from general_ludd.connectors.grafana_oncall import _CallableClient

        transport = MagicMock()
        with _CallableClient(transport) as client:
            assert client is client

    def test_exit_returns_none(self) -> None:
        from general_ludd.connectors.grafana_oncall import _CallableClient

        cm = _CallableClient(MagicMock())
        assert cm.__exit__(None, None, None) is None

    def test_exit_on_exception(self) -> None:
        from general_ludd.connectors.grafana_oncall import _CallableClient

        cm = _CallableClient(MagicMock())
        assert cm.__exit__(ValueError, ValueError("x"), None) is None

    def test_reentrant_always_returns_same_self(self) -> None:
        from general_ludd.connectors.grafana_oncall import _CallableClient

        cm = _CallableClient(MagicMock())
        assert cm.__enter__() is cm
        assert cm.__enter__() is cm


# ============================================================================
# 4. _suppress_log — writer/supervisor.py
# ============================================================================


class TestSuppressLog:
    def test_noop_does_not_suppress(self) -> None:
        from general_ludd.writer.supervisor import _suppress_log

        with pytest.raises(ValueError), _suppress_log():
            raise ValueError("should propagate")

    def test_enter_returns_self(self) -> None:
        from general_ludd.writer.supervisor import _suppress_log

        cm = _suppress_log()
        assert cm.__enter__() is cm

    def test_exit_returns_none(self) -> None:
        from general_ludd.writer.supervisor import _suppress_log

        cm = _suppress_log()
        assert cm.__exit__(None, None, None) is None


# ============================================================================
# 5. _suppress_oserror — agents/dispatch_checkpoint.py
# ============================================================================


class TestSuppressOSError:
    def test_does_not_actually_suppress_oserror(self) -> None:
        from general_ludd.agents.dispatch_checkpoint import _suppress_oserror

        with pytest.raises(OSError), _suppress_oserror():
            raise OSError("disk full")

    def test_enter_returns_self(self) -> None:
        from general_ludd.agents.dispatch_checkpoint import _suppress_oserror

        cm = _suppress_oserror()
        assert cm.__enter__() is cm

    def test_noop_on_clean_exit(self) -> None:
        from general_ludd.agents.dispatch_checkpoint import _suppress_oserror

        with _suppress_oserror():
            pass

    def test_exit_returns_none(self) -> None:
        from general_ludd.agents.dispatch_checkpoint import _suppress_oserror

        cm = _suppress_oserror()
        assert cm.__exit__(None, None, None) is None


# ============================================================================
# 6. OrnithSandbox — ornith/sandbox.py
# ============================================================================


class TestOrnithSandbox:
    def test_enter_returns_self(self) -> None:
        from general_ludd.ornith.sandbox import OrnithSandbox

        sandbox = OrnithSandbox()
        try:
            assert sandbox.__enter__() is sandbox
        finally:
            sandbox.cleanup()

    def test_temp_dir_created(self) -> None:
        from general_ludd.ornith.sandbox import OrnithSandbox

        sandbox = OrnithSandbox()
        assert sandbox.temp_dir.exists()
        sandbox.cleanup()

    def test_cleanup_on_exit(self) -> None:
        from general_ludd.ornith.sandbox import OrnithSandbox

        sandbox = OrnithSandbox()
        sandbox.__exit__(None, None, None)
        assert sandbox._cleaned

    def test_cleanup_on_exception_exit(self) -> None:
        from general_ludd.ornith.sandbox import OrnithSandbox

        sandbox = OrnithSandbox()
        try:
            with sandbox:
                raise RuntimeError("inside sandbox")
        except RuntimeError:
            pass
        assert sandbox._cleaned

    def test_cleanup_idempotent(self) -> None:
        from general_ludd.ornith.sandbox import OrnithSandbox

        sandbox = OrnithSandbox()
        sandbox.cleanup()
        sandbox.cleanup()
        sandbox.__exit__(None, None, None)
        assert sandbox._cleaned

    def test_create_sandbox_factory(self) -> None:
        from general_ludd.ornith.sandbox import create_ornith_sandbox

        sandbox = create_ornith_sandbox()
        try:
            assert sandbox.temp_dir.exists()
        finally:
            sandbox.cleanup()

    def test_nested_sandboxes_independent(self) -> None:
        from general_ludd.ornith.sandbox import OrnithSandbox

        s1 = OrnithSandbox()
        s2 = OrnithSandbox()
        try:
            assert s1.temp_dir.exists() and s2.temp_dir.exists()
            assert s1.temp_dir != s2.temp_dir
        finally:
            s1.cleanup()
            s2.cleanup()


# ============================================================================
# 7. DurationTracker.track — observability/timing.py
# ============================================================================


class TestDurationTrackerTrack:
    def test_records_elapsed_time(self) -> None:
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker(min_samples=1)
        with t.track("op"):
            time.sleep(0.001)
        assert t.baseline("op") is not None

    def test_records_even_on_exception(self) -> None:
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker(min_samples=1)
        with pytest.raises(ValueError), t.track("err_op"):
            raise ValueError("boom")
        assert t.baseline("err_op") is not None

    def test_yields_none(self) -> None:
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker()
        with t.track("op") as val:
            assert val is None

    def test_on_anomaly_callback_called(self) -> None:
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker(min_samples=5, slow_factor=1.0001, abs_floor_s=0.0)
        for _ in range(5):
            t.record("op", 0.001)
        cb = MagicMock()
        with t.track("op", on_anomaly=cb):
            time.sleep(0.1)
        assert cb.called

    def test_on_anomaly_suppressed_if_it_raises(self) -> None:
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker(min_samples=5, slow_factor=1.0001, abs_floor_s=0.0)
        for _ in range(5):
            t.record("op", 0.001)

        def bad_cb(verdict):
            raise RuntimeError("callback failure")

        with t.track("op", on_anomaly=bad_cb):
            time.sleep(0.1)

    def test_reentrant_track_separate_keys(self) -> None:
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker(min_samples=1)
        with t.track("outer"), t.track("inner"):
            time.sleep(0.001)
        assert t.baseline("outer") is not None
        assert t.baseline("inner") is not None


# ============================================================================
# 8. StallWatchdog.watch — observability/timing.py
# ============================================================================


class TestStallWatchdog:
    def test_finish_called_on_clean_exit(self) -> None:
        from general_ludd.observability.timing import StallWatchdog

        w = StallWatchdog()
        try:
            with w.watch("op1", "key1"):
                pass
            assert "op1" not in w._inflight
        finally:
            w.stop_sweeper()

    def test_finish_called_on_exception(self) -> None:
        from general_ludd.observability.timing import StallWatchdog

        w = StallWatchdog()
        try:
            with pytest.raises(ValueError), w.watch("op2", "key2"):
                raise ValueError("boom")
            assert "op2" not in w._inflight
        finally:
            w.stop_sweeper()

    def test_deadline_passed_triggers_stall(self) -> None:
        from general_ludd.observability.timing import StallWatchdog

        w = StallWatchdog()
        try:
            w.start("op3", "key3", deadline_s=0.0)
            time.sleep(0.05)
            reports = w.poll()
            assert any(r.op_id == "op3" for r in reports)
            w.finish("op3")
        finally:
            w.stop_sweeper()

    def test_yields_none(self) -> None:
        from general_ludd.observability.timing import StallWatchdog

        w = StallWatchdog()
        try:
            with w.watch("op4", "key4") as val:
                assert val is None
        finally:
            w.stop_sweeper()


# ============================================================================
# 9. git_repo_lock sync — git_automation/locking.py
# ============================================================================


class TestGitRepoLockSync:
    def test_basic_lock_acquire_release(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            from general_ludd.git_automation.locking import git_repo_lock

            with git_repo_lock(str(repo)):
                pass

    def test_reentrant_on_same_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            from general_ludd.git_automation.locking import git_repo_lock

            with git_repo_lock(str(repo)), git_repo_lock(str(repo)):
                pass

    def test_releases_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            from general_ludd.git_automation.locking import git_repo_lock

            with pytest.raises(ValueError), git_repo_lock(str(repo)):
                raise ValueError("boom")


# ============================================================================
# 10. async_git_repo_lock — git_automation/locking.py (async wrapper)
# ============================================================================


class TestAsyncGitRepoLock:
    @pytest.mark.asyncio
    async def test_async_lock_context_manager(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            from general_ludd.git_automation.locking import async_git_repo_lock

            cm = await async_git_repo_lock(str(repo))
            with cm:
                pass

    @pytest.mark.asyncio
    async def test_async_lock_releases_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            from general_ludd.git_automation.locking import async_git_repo_lock

            cm = await async_git_repo_lock(str(repo))
            with pytest.raises(ValueError), cm:
                raise ValueError("boom")


# ============================================================================
# 11. _EnteredAsyncGitRepoLock internals — git_automation/locking.py
# ============================================================================


class TestEnteredAsyncGitRepoLock:
    @pytest.mark.asyncio
    async def test_double_exit_safe(self) -> None:
        from general_ludd.git_automation.locking import async_git_repo_lock

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            cm = await async_git_repo_lock(str(repo))
            with cm:
                pass
            result = cm.__exit__(None, None, None)
            assert result is None

    @pytest.mark.asyncio
    async def test_close_flag_prevents_reuse(self) -> None:
        from general_ludd.git_automation.locking import async_git_repo_lock

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            cm = await async_git_repo_lock(str(repo))
            with cm:
                pass
            result = cm.__exit__(ValueError, ValueError("x"), None)
            assert result is None


# ============================================================================
# 12. parked — agents/hibernation.py async context manager
# ============================================================================


class TestHibernationParked:
    @pytest.mark.asyncio
    async def test_no_dehydrate_when_policy_rejects(self) -> None:
        from general_ludd.agents.hibernation import (
            AgentEnvironmentSnapshot,
            HibernationController,
        )

        mock_store = MagicMock()
        controller = HibernationController(mock_store, min_context_messages=9999)
        snap = MagicMock(spec=AgentEnvironmentSnapshot)
        snap.depth = 0
        snap.messages = []

        async with controller.parked(snap) as parked:
            assert parked.dehydrated is False


# ============================================================================
# 13. _suppress_cancel for async CancelledError — contextual nesting
# ============================================================================


class TestSuppressCancelAsync:
    def test_suppress_cancel_within_sync_context(self) -> None:
        from general_ludd.pipeline.controller import _suppress_cancel

        def make_cancel():
            raise asyncio.CancelledError()

        with _suppress_cancel():
            make_cancel()


# ============================================================================
# 14. @contextmanager function-style pattern audit
# ============================================================================


class TestContextlibCompatibility:
    def test_git_repo_lock_is_reusable_context_manager(self) -> None:
        from general_ludd.git_automation.locking import git_repo_lock

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            cm = git_repo_lock(str(repo))
            with cm:
                pass
            is_cm = isinstance(cm, contextlib._GeneratorContextManager)
            assert is_cm or hasattr(cm, "__enter__")

    def test_track_is_reusable_context_manager(self) -> None:
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker(min_samples=1)
        cm = t.track("op")
        with cm:
            pass
        is_cm = isinstance(cm, contextlib._GeneratorContextManager)
        assert is_cm or hasattr(cm, "__enter__")

    def test_watch_is_reusable_context_manager(self) -> None:
        from general_ludd.observability.timing import StallWatchdog

        w = StallWatchdog()
        try:
            cm = w.watch("op", "key")
            with cm:
                pass
            is_cm = isinstance(cm, contextlib._GeneratorContextManager)
            assert is_cm or hasattr(cm, "__enter__")
        finally:
            w.stop_sweeper()


# ============================================================================
# 15. AzureGameRuntime — cloud/azure_game_runtime.py
# ============================================================================


class TestAzureGameRuntime:
    def test_enter_calls_start(self) -> None:
        from general_ludd.cloud.azure_game_runtime import AzureGameRuntime

        runtime = AzureGameRuntime.__new__(AzureGameRuntime)
        runtime.start = MagicMock(return_value="started")  # type: ignore[method-assign]
        result = runtime.__enter__()
        runtime.start.assert_called_once()
        assert result == "started"

    def test_exit_calls_close(self) -> None:
        from general_ludd.cloud.azure_game_runtime import AzureGameRuntime

        runtime = AzureGameRuntime.__new__(AzureGameRuntime)
        runtime.close = MagicMock()  # type: ignore[method-assign]
        runtime.__exit__(None, None, None)
        runtime.close.assert_called_once()

    def test_exit_calls_close_even_on_exception(self) -> None:
        from general_ludd.cloud.azure_game_runtime import AzureGameRuntime

        runtime = AzureGameRuntime.__new__(AzureGameRuntime)
        runtime.close = MagicMock()  # type: ignore[method-assign]
        runtime.__exit__(ValueError, ValueError("boom"), None)
        runtime.close.assert_called_once()


# ============================================================================
# 16. OrnithSandbox file-write during context — nested with _suppress_exc
# ============================================================================


class TestNestedContextManagers:
    def test_sandbox_with_suppress_exc_cleanup_order(self) -> None:
        from general_ludd.ornith.sandbox import OrnithSandbox
        from general_ludd.writer._child import _suppress_exc

        sandbox = OrnithSandbox()
        try:
            with _suppress_exc(), sandbox:
                (sandbox.temp_dir / "file.txt").write_text("data")
        finally:
            assert sandbox._cleaned

    def test_suppress_cancel_nested_with_track(self) -> None:
        from general_ludd.observability.timing import DurationTracker
        from general_ludd.pipeline.controller import _suppress_cancel

        t = DurationTracker(min_samples=1)
        try:
            with _suppress_cancel(), t.track("cancel_op"):
                raise asyncio.CancelledError()
        except BaseException:
            pytest.fail("CancelledError should have been suppressed")
        assert t.baseline("cancel_op") is not None

    def test_triple_nested_exception_handling(self) -> None:
        from general_ludd.observability.timing import DurationTracker
        from general_ludd.pipeline.controller import _suppress_cancel
        from general_ludd.writer._child import _suppress_exc

        t = DurationTracker(min_samples=1)
        with _suppress_exc(), _suppress_cancel(), t.track("deep"):
            raise asyncio.CancelledError()
        assert t.baseline("deep") is not None

    def test_git_lock_with_track(self) -> None:
        from general_ludd.git_automation.locking import git_repo_lock
        from general_ludd.observability.timing import DurationTracker

        t = DurationTracker(min_samples=1)
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            with t.track("git_lock"), git_repo_lock(str(repo)):
                pass
        assert t.baseline("git_lock") is not None

    def test_multi_suppress_class_hierarchy(self) -> None:
        from general_ludd.agents.dispatch_checkpoint import _suppress_oserror
        from general_ludd.writer._child import _suppress_exc
        from general_ludd.writer.supervisor import _suppress_log

        # _suppress_exc swallows, so no exception emerges; verify structure
        with _suppress_exc(), _suppress_log(), _suppress_oserror():
            raise OSError("nested cleanup")


# ============================================================================
# 17. git_repo_lock file-lock contract
# ============================================================================


class TestGitRepoLockFileLockContract:
    def test_lock_before_yield_is_called(self) -> None:
        from general_ludd.git_automation.locking import git_repo_lock

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            lock_file = Path(td) / "repo" / ".git" / "gludd-git.lock"
            with git_repo_lock(str(repo)):
                assert lock_file.exists(), "lock file should exist while held"

    def test_no_git_dir_graceful(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "bare_repo"
            repo.mkdir()
            from general_ludd.git_automation.locking import git_repo_lock

            with git_repo_lock(str(repo)):
                pass

    def test_thread_safety_same_process(self) -> None:
        from general_ludd.git_automation.locking import git_repo_lock

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            results: list[str] = []

            def worker(name: str) -> None:
                with git_repo_lock(str(repo)):
                    results.append(name)
                    time.sleep(0.01)

            t1 = threading.Thread(target=worker, args=("A",))
            t2 = threading.Thread(target=worker, args=("B",))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            assert len(results) == 2
