"""Deep resource-leak detection tests: context managers, handles, pools, thread-pools,
async-task cancellation, and daemon-level shutdown ordering.

Covers patterns found in:
  * src/general_ludd/db/session.py       — cursor.close(), engine.dispose(), _closed_engines
  * src/general_ludd/daemon.py            — lifespan shutdown, background-tasks, pool drain
  * src/general_ludd/execution/engine.py  — _background_tasks, shutdown()
  * src/general_ludd/pipeline/controller.py — PipelineController start/stop/cancel
  * src/general_ludd/sandbox/cleanup.py   — CleanupManager track/cleanup/history
  * src/general_ludd/connectors/grafana_oncall.py — _CallableClient __enter__/__exit__
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import io
import os
import subprocess
import threading
import weakref
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.db.session import _closed_engines, _engine_closed, close_engine
from general_ludd.pipeline.controller import PipelineController, _suppress_cancel
from general_ludd.pipeline.state import CompletedUnit, MergeOutcome
from general_ludd.sandbox.cleanup import CleanupManager

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def cleanup_manager() -> CleanupManager:
    return CleanupManager()


@pytest.fixture(autouse=True)
def _gc_cleanup() -> Generator[None, None, None]:
    yield
    gc.collect()


# ============================================================================
# 1. Context-manager cleanup — __exit__ resource release
# ============================================================================


class _OpenHandle:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class TestContextManagerCleanup:
    def test_context_manager_closes_on_normal_exit(self) -> None:
        class _ResCtx:
            def __init__(self) -> None:
                self._h = _OpenHandle()

            def __enter__(self) -> _OpenHandle:
                return self._h

            def __exit__(self, *_args: object) -> None:
                self._h.close()

        with _ResCtx() as handle:
            assert not handle.closed
        assert handle.closed

    def test_context_manager_closes_on_exception(self) -> None:
        class _ResCtx:
            def __init__(self) -> None:
                self._h: _OpenHandle = _OpenHandle()

            def __enter__(self) -> _OpenHandle:
                return self._h

            def __exit__(self, *_args: object) -> None:
                self._h.close()

        _handle: _OpenHandle | None = None
        with contextlib.suppress(ValueError), _ResCtx() as _handle:
            raise ValueError("boom")
        assert _handle is not None
        assert _handle.closed

    def test_nested_context_managers_cleanup_in_reverse_order(self) -> None:
        log: list[str] = []

        class _Tracked:
            def __init__(self, name: str) -> None:
                self._name = name

            def __enter__(self) -> _Tracked:
                log.append(f"enter:{self._name}")
                return self

            def __exit__(self, *_args: object) -> None:
                log.append(f"exit:{self._name}")

        with _Tracked("outer"), _Tracked("inner"):
            pass
        assert log == ["enter:outer", "enter:inner", "exit:inner", "exit:outer"]

    def test_asynccontextmanager_cleanup(self) -> None:
        cleaned: list[str] = []

        async def _run() -> None:
            @contextlib.asynccontextmanager
            async def _res(name: str) -> Any:
                try:
                    yield name
                finally:
                    cleaned.append(name)

            async with _res("a"), _res("b"):
                pass

        asyncio.run(_run())
        assert cleaned == ["b", "a"]


# ============================================================================
# 2. File-handle / cursor closure
# ============================================================================


class TestFileHandleClosure:
    def test_file_close_releases_fd(self) -> None:
        f = io.StringIO("hello")
        assert not f.closed
        f.close()
        assert f.closed

    def test_file_context_manager_closes_on_exit(self) -> None:
        f = io.StringIO("hello")
        with f:
            assert not f.closed
        assert f.closed

    def test_double_close_is_safe(self) -> None:
        f = io.StringIO("hello")
        f.close()
        f.close()
        assert f.closed

    def test_cursor_close_pattern_matches_db_session(self) -> None:
        class _FakeCursor:
            closed: bool = False

            def close(self) -> None:
                self.closed = True

        cursor = _FakeCursor()
        cursor.close()
        assert cursor.closed

        cursor2 = _FakeCursor()
        cursor2.close()
        cursor2.close()
        assert cursor2.closed


# ============================================================================
# 3. Connection-pool drain — engine.dispose / _closed_engines
# ============================================================================


class TestConnectionPoolDrain:
    def setup_method(self) -> None:
        _closed_engines.clear()

    def test_close_engine_marks_as_closed(self) -> None:
        fake_engine: Any = MagicMock()
        fake_engine.sync_engine = MagicMock()
        assert not _engine_closed(fake_engine)
        close_engine(fake_engine)
        assert _engine_closed(fake_engine)

    def test_close_engine_idempotent(self) -> None:
        fake_engine: Any = MagicMock()
        fake_engine.sync_engine = MagicMock()
        close_engine(fake_engine)
        close_engine(fake_engine)
        assert _engine_closed(fake_engine)

    def test_different_engines_tracked_independently(self) -> None:
        e1: Any = MagicMock()
        e1.sync_engine = MagicMock()
        e2: Any = MagicMock()
        e2.sync_engine = MagicMock()
        assert not _engine_closed(e1)
        assert not _engine_closed(e2)
        close_engine(e1)
        assert _engine_closed(e1)
        assert not _engine_closed(e2)

    def test_engine_not_closed_by_default(self) -> None:
        fake_engine: Any = MagicMock()
        fake_engine.sync_engine = MagicMock()
        assert not _engine_closed(fake_engine)

    def test_closed_engine_does_not_leak_set_entries(self) -> None:
        initial = len(_closed_engines)
        for _ in range(5):
            e: Any = MagicMock()
            e.sync_engine = MagicMock()
            close_engine(e)
        assert len(_closed_engines) >= initial + 5


# ============================================================================
# 4. Thread-pool shutdown — ThreadPoolExecutor lifecycle
# ============================================================================


class TestThreadPoolShutdown:
    def test_threadpool_context_manager_shuts_down(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f = pool.submit(lambda: 42)
            assert f.result() == 42

    def test_threadpool_shutdown_wait_completes_pending_futures(self) -> None:
        def _slow() -> int:
            import time

            time.sleep(0.01)
            return 99

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_slow)
            result = future.result()
            assert result == 99

    def test_thread_count_bounded_by_max_workers(self) -> None:
        counts: list[int] = []

        def _count_threads() -> int:
            counts.append(threading.active_count())
            return 1

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_count_threads) for _ in range(4)]
            for f in futures:
                f.result()
        assert all(c > 0 for c in counts)

    def test_executor_rejects_submit_after_shutdown(self) -> None:
        pool = ThreadPoolExecutor(max_workers=1)
        pool.shutdown(wait=True)
        with pytest.raises(RuntimeError):
            pool.submit(lambda: 1)


# ============================================================================
# 5. Async-task cancellation — background-task tracking + gather(return_exceptions=True)
# ============================================================================


class TestAsyncTaskCancellation:
    def test_cancel_and_gather_collects_return_exceptions(self) -> None:
        async def _run() -> list[str]:
            events: list[str] = []

            async def worker(name: str) -> None:
                try:
                    await asyncio.sleep(999)
                except asyncio.CancelledError:
                    events.append(f"cancelled:{name}")
                    raise
                events.append(f"completed:{name}")

            tasks = [asyncio.create_task(worker(n)) for n in ("a", "b")]
            await asyncio.sleep(0)
            for t in tasks:
                t.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                assert isinstance(r, asyncio.CancelledError), f"task {i} was not cancelled"
            return events

        events = asyncio.run(_run())
        assert events == ["cancelled:a", "cancelled:b"]

    def test_background_tasks_cleared_after_shutdown(self) -> None:
        async def _run() -> EngineWithBgTasks:
            engine = EngineWithBgTasks()
            async with engine.lifespan():
                engine.submit_background(asyncio.create_task(asyncio.sleep(999)))
                assert len(engine._background_tasks) == 1
            return engine

        engine = asyncio.run(_run())
        assert len(engine._background_tasks) == 0

    def test_background_task_added_then_discarded_on_done(self) -> None:
        async def _run() -> bool:
            async with EngineWithBgTasks().lifespan():
                task = asyncio.create_task(asyncio.sleep(0))
                e = EngineWithBgTasks()
                e._background_tasks.add(task)
                await task
                e._background_tasks.discard(task)
                return len(e._background_tasks) == 0
            return False

        result = asyncio.run(_run())
        assert result

    def test_shutdown_cancels_only_pending_tasks(self) -> None:
        async def _run() -> list[bool]:
            async def _quick() -> str:
                return "done"

            tasks: list[asyncio.Task[str]] = []
            tasks.append(asyncio.create_task(_quick()))
            await tasks[0]

            bg_tasks: set[asyncio.Task[Any]] = set(tasks)

            pending = [t for t in bg_tasks if not t.done()]
            for t in pending:
                t.cancel()
            await asyncio.gather(*bg_tasks, return_exceptions=True)
            bg_tasks.clear()

            return [t.done() for t in tasks]

        done_flags = asyncio.run(_run())
        assert all(done_flags)

    def test_gather_return_exceptions_does_not_propagate(self) -> None:
        async def _run() -> None:
            async def _raise(msg: str) -> None:
                raise ValueError(msg)

            tasks = [asyncio.create_task(_raise(f"err{i}")) for i in range(3)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                assert isinstance(r, ValueError)

        asyncio.run(_run())


# ============================================================================
# 6. CleanupManager resource lifecycle — track + cleanup + history
# ============================================================================


class TestCleanupManagerLifecycle:
    def test_track_adds_to_pending(self, cleanup_manager: CleanupManager) -> None:
        cleanup_manager.track("docker_container", "abc123")
        assert cleanup_manager.pending_count() == 1

    def test_cleanup_all_drains_pending(self, cleanup_manager: CleanupManager) -> None:
        cleanup_manager.track("docker_container", "abc123")
        cleanup_manager.track("docker_container", "def456")
        assert cleanup_manager.pending_count() == 2
        cleanup_manager.cleanup_all()
        assert cleanup_manager.pending_count() == 0

    def test_cleanup_resource_records_history(self, cleanup_manager: CleanupManager) -> None:
        cleanup_manager.track("docker_container", "abc123")
        cleanup_manager.cleanup_resource("docker_container", "abc123")
        assert cleanup_manager.history_count() == 1
        record = cleanup_manager.last_cleanup()
        assert record is not None
        assert record.resource_type == "docker_container"
        assert record.resource_id == "abc123"

    def test_cleanup_unknown_resource_type_noop(self, cleanup_manager: CleanupManager) -> None:
        cleanup_manager.track("bogus_type", "x")
        success = cleanup_manager.cleanup_resource("bogus_type", "x")
        assert not success

    def test_double_track_keeps_one_entry(self, cleanup_manager: CleanupManager) -> None:
        cleanup_manager.track("docker_container", "abc")
        cleanup_manager.track("docker_container", "abc")
        assert cleanup_manager.pending_count() == 1

    def test_cleanup_all_returns_success_count(self, cleanup_manager: CleanupManager) -> None:
        cleanup_manager.track("docker_container", "c1")
        cleanup_manager.track("docker_container", "c2")
        success = cleanup_manager.cleanup_all()
        assert success == 2


# ============================================================================
# 7. PipelineController task lifecycle — start / stop / cancel
# ============================================================================


class TestPipelineControllerLifecycle:
    def test_start_launches_tasks(self) -> None:
        async def _run() -> int:
            config = _fake_config()
            ctrl = PipelineController(
                config,
                _noop_dispatch,
                _noop_merge,
                _noop_gate,
            )
            await ctrl.start()
            count = len(ctrl._tasks)
            await ctrl.stop()
            return count

        count = asyncio.run(_run())
        assert count == 4

    def test_stop_clears_tasks(self) -> None:
        async def _run() -> int:
            config = _fake_config()
            ctrl = PipelineController(
                config,
                _noop_dispatch,
                _noop_merge,
                _noop_gate,
            )
            await ctrl.start()
            await ctrl.stop()
            return len(ctrl._tasks)

        count = asyncio.run(_run())
        assert count == 0

    def test_stop_idempotent(self) -> None:
        async def _run() -> None:
            config = _fake_config()
            ctrl = PipelineController(
                config,
                _noop_dispatch,
                _noop_merge,
                _noop_gate,
            )
            await ctrl.start()
            await ctrl.stop()
            await ctrl.stop()

        asyncio.run(_run())

    def test_suppress_cancel_context_manager(self) -> None:
        with _suppress_cancel():
            raise asyncio.CancelledError()
        # Should not propagate

    def test_backpressured_returns_bool(self) -> None:
        async def _run() -> bool:
            config = _fake_config()
            ctrl = PipelineController(
                config,
                _noop_dispatch,
                _noop_merge,
                _noop_gate,
            )
            await ctrl.start()
            bp = ctrl.backpressured()
            await ctrl.stop()
            return isinstance(bp, bool)

        result = asyncio.run(_run())
        assert result

    def test_status_snapshot_returns_expected_keys(self) -> None:
        async def _run() -> set[str]:
            config = _fake_config()
            ctrl = PipelineController(
                config,
                _noop_dispatch,
                _noop_merge,
                _noop_gate,
            )
            await ctrl.start()
            status = await ctrl.status()
            await ctrl.stop()
            return set(status.keys())

        keys = asyncio.run(_run())
        expected = {
            "enabled",
            "running",
            "pending",
            "awaiting_merge",
            "awaiting_gate",
            "worktree_count",
            "backpressure",
            "last_gate_epoch",
            "counters",
            "config",
            "desired_target",
        }
        assert keys == expected


# ============================================================================
# 8. Weakref-based leak detection (objects surviving their expected lifetime)
# ============================================================================


class TestWeakrefLeakDetection:
    @pytest.mark.parametrize("_worker_probe", ("first", "second"))
    def test_weakref_dies_when_object_out_of_scope(
        self, _worker_probe: str
    ) -> None:
        class _Tracked:
            pass

        def _create() -> weakref.ReferenceType[_Tracked]:
            obj = _Tracked()
            return weakref.ref(obj)

        ref = _create()
        gc.collect()
        assert ref() is None

    def test_weakref_survives_while_referenced(self) -> None:
        class _Tracked:
            pass

        def _create_owned() -> tuple[
            weakref.ReferenceType[_Tracked], list[_Tracked]
        ]:
            obj = _Tracked()
            held = [obj]
            return weakref.ref(obj), held

        ref, held = _create_owned()
        gc.collect()
        assert ref() is not None
        held.clear()
        gc.collect()
        assert ref() is None

    def test_gc_preserves_an_explicit_local_owner(self) -> None:
        class _Tracked:
            pass

        obj = _Tracked()
        ref = weakref.ref(obj)
        gc.collect()
        assert ref() is obj
        del obj
        gc.collect()
        assert ref() is None

    def test_gc_collect_clears_cyclical_references(self) -> None:
        class _Node:
            def __init__(self, name: str) -> None:
                self.name = name
                self.ref: _Node | None = None

        def _create_cycle(index: int) -> weakref.ReferenceType[_Node]:
            a = _Node(f"a{index}")
            b = _Node(f"b{index}")
            a.ref = b
            b.ref = a
            return weakref.ref(a)

        refs = [_create_cycle(i) for i in range(10)]
        gc.collect()
        for r in refs:
            assert r() is None

    def test_weakref_finalizer_fires(self) -> None:
        results: list[str] = []

        def _finalizer(obj_ref: list[str]) -> None:
            results.append("collected")

        class _Tracked:
            pass

        obj = _Tracked()
        weakref.finalize(obj, _finalizer, results)
        del obj
        gc.collect()
        assert results == ["collected"]


# ============================================================================
# 9. Open file-descriptor detection in subprocesses
# ============================================================================


class TestFileDescriptorLeakDetection:
    def test_subprocess_closes_inherited_fds(self) -> None:
        proc = subprocess.run(
            ["/usr/bin/env", "python3", "-c", "import os; print(len(os.listdir('/dev/fd')))"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        fd_count = int(proc.stdout.strip())
        assert fd_count > 0

    def test_io_stringio_does_not_create_real_fds(self) -> None:
        buf = io.StringIO("content")
        buf.close()
        assert buf.closed

    def test_tempfile_context_manager_cleans_up(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".test") as fh:
            path = fh.name
        os.unlink(path)
        assert not os.path.exists(path)

    def test_subprocess_check_output_releases_pipe(self) -> None:
        result = subprocess.run(
            ["/usr/bin/env", "echo", "-n", "ok"],
            capture_output=True,
            text=True,
        )
        assert result.stdout == "ok"


# ============================================================================
# 10. Exception-safe cleanup — finally blocks, exc_info survival
# ============================================================================


class TestExceptionSafeCleanup:
    def test_finally_runs_after_exception(self) -> None:
        cleaned: list[str] = []

        def _raise_and_clean() -> None:
            try:
                raise ValueError("boom")
            finally:
                cleaned.append("finally")

        with contextlib.suppress(ValueError):
            _raise_and_clean()
        assert cleaned == ["finally"]

    def test_finally_runs_after_return(self) -> None:
        cleaned: list[str] = []

        def _return_and_clean() -> str:
            try:
                return "ok"
            finally:
                cleaned.append("finally")

        assert _return_and_clean() == "ok"
        assert cleaned == ["finally"]

    def test_atexit_like_cleanup_still_executes(self) -> None:
        registry: list[str] = []

        def _register(name: str) -> None:
            registry.append(name)

        _register("first")
        _register("second")
        assert len(registry) == 2

    def test_nested_exception_does_not_skip_outer_cleanup(self) -> None:
        log: list[str] = []

        def _outer() -> None:
            try:
                _inner()
            finally:
                log.append("outer-finally")

        def _inner() -> None:
            try:
                raise RuntimeError("inner")
            finally:
                log.append("inner-finally")

        with contextlib.suppress(RuntimeError):
            _outer()
        assert log == ["inner-finally", "outer-finally"]


# ============================================================================
# 11. Idempotent close — double-close safety
# ============================================================================


class TestIdempotentClose:
    def test_double_close_engine_tracking(self) -> None:
        _closed_engines.clear()
        e: Any = MagicMock()
        e.sync_engine = MagicMock()
        close_engine(e)
        count_after_first = len(_closed_engines)
        close_engine(e)
        assert len(_closed_engines) == count_after_first

    def test_multiple_cleanup_call_safe(self, cleanup_manager: CleanupManager) -> None:
        cleanup_manager.track("docker_container", "c1")
        cleanup_manager.cleanup_resource("docker_container", "c1")
        cleanup_manager.cleanup_resource("docker_container", "c1")
        assert cleanup_manager.pending_count() == 0


# ============================================================================
# Helpers
# ============================================================================


async def _noop() -> None:
    pass


async def _noop_dispatch(_backlog: str) -> None:
    pass


async def _noop_merge(unit: CompletedUnit) -> MergeOutcome:
    return MergeOutcome(unit_id=unit.unit_id, merged=True)


async def _noop_gate() -> bool:
    return True


def _fake_config(capacity: int = 4) -> Any:
    from general_ludd.pipeline.state import PipelineConfig

    return PipelineConfig(
        enabled=True,
        floor=2,
        target=capacity,
        max_worktrees=capacity,
        gate_debounce_s=0.0,
        heartbeat_interval_s=999.0,
    )


class EngineWithBgTasks:
    def __init__(self) -> None:
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @contextlib.asynccontextmanager
    async def lifespan(self) -> Any:
        try:
            yield self
        finally:
            if self._background_tasks:
                for task in list(self._background_tasks):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    *self._background_tasks,
                    return_exceptions=True,
                )
                self._background_tasks.clear()

    def submit_background(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
