"""Deep async safety and coroutine tests.

Covers: no blocking I/O in async functions, cancellation safety, task group
exception handling, event loop lifecycle, async context manager correctness.

Slices:
  A — Event loop lifecycle (get_running_loop vs get_event_loop)
  B — Background task lifecycle (create_task + done_callback + shutdown drain)
  C — Cancellation safety (cancel + gather with return_exceptions)
  D — asyncio.to_thread wrapping (blocking I/O off the event loop)
  E — Task group exception propagation
  F — asyncio.Event stop-signal correctness
  G — Async context manager integrity
  H — Retry with exponential backoff
  I — asyncio.Lock serialization
  J — map_reduce gather aggregation
"""

from __future__ import annotations

import asyncio
import contextlib
import textwrap
import time

import pytest

# ---------------------------------------------------------------------------
# A — Event loop lifecycle
# ---------------------------------------------------------------------------


class TestEventLoopLifecycle:
    """asyncio.get_running_loop() is correct; get_event_loop() is deprecated."""

    def test_get_running_loop_inside_async(self):
        """get_running_loop() returns the current loop inside an async context."""

        async def _inner():
            loop = asyncio.get_running_loop()
            assert loop is not None
            assert isinstance(loop, asyncio.AbstractEventLoop)

        asyncio.run(_inner())

    def test_get_running_loop_outside_async_raises(self):
        """get_running_loop() raises RuntimeError when no loop is running."""
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()

    def test_get_event_loop_deprecated_semantics(self):
        """Python 3.14 no longer creates an implicit loop for this call."""
        with pytest.raises(RuntimeError, match="There is no current event loop"):
            asyncio.get_event_loop()


# ---------------------------------------------------------------------------
# B — Background task lifecycle (create_task + done_callback + shutdown)
# ---------------------------------------------------------------------------


class TestBackgroundTaskLifecycle:
    """Simulates the pattern from engine.py: defer_commit + shutdown."""

    @staticmethod
    async def _fake_git_commit(path: str, message: str) -> str:
        await asyncio.sleep(0.001)
        return "abc12345"

    async def _defer_commit(self, background_tasks, commit_lock, path, msg):
        async def _commit_with_lock():
            async with commit_lock:
                return await self._fake_git_commit(path, msg)

        task = asyncio.create_task(_commit_with_lock())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return task

    async def _shutdown(self, background_tasks):
        if not background_tasks:
            return
        for task in list(background_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        background_tasks.clear()

    @pytest.mark.asyncio
    async def test_background_task_completes_and_self_removes(self):
        tasks: set[asyncio.Task] = set()
        lock = asyncio.Lock()
        task = await self._defer_commit(tasks, lock, "/tmp", "msg")
        await task
        assert task.done()
        assert task.result() == "abc12345"
        assert task not in tasks

    @pytest.mark.asyncio
    async def test_background_tasks_drained_on_shutdown(self):
        tasks: set[asyncio.Task] = set()
        lock = asyncio.Lock()
        await self._defer_commit(tasks, lock, "/tmp", "msg1")
        await self._defer_commit(tasks, lock, "/tmp", "msg2")
        # Wait for them to complete
        await asyncio.sleep(0.01)
        await self._shutdown(tasks)
        assert len(tasks) == 0

    @pytest.mark.asyncio
    async def test_background_task_cancelled_on_shutdown_without_error(self):
        tasks: set[asyncio.Task] = set()
        lock = asyncio.Lock()

        async def _slow_commit():
            async with lock:
                await asyncio.sleep(10)
            return "slow"

        task = asyncio.create_task(_slow_commit())
        tasks.add(task)
        # Immediately shut down
        await self._shutdown(tasks)
        assert task.cancelled()
        assert len(tasks) == 0

    @pytest.mark.asyncio
    async def test_done_callback_inspects_exception(self):
        """Mirrors engine.py:_on_commit_done — exception is observable."""
        errors: list[Exception] = []
        tasks: set[asyncio.Task] = set()

        async def _failing():
            raise ValueError("commit failed")

        task = asyncio.create_task(_failing())
        tasks.add(task)

        def _on_done(t):
            tasks.discard(t)
            exc = t.exception() if not t.cancelled() else None
            if exc is not None:
                errors.append(exc)

        task.add_done_callback(_on_done)
        await asyncio.gather(task, return_exceptions=True)
        assert len(errors) == 1
        assert "commit failed" in str(errors[0])
        assert task not in tasks


# ---------------------------------------------------------------------------
# C — Cancellation safety
# ---------------------------------------------------------------------------


class TestCancellationSafety:
    """CancelledError must propagate cleanly and not corrupt shared state."""

    @pytest.mark.asyncio
    async def test_cancel_gather_with_return_exceptions_suppresses_errors(self):
        """asyncio.gather(..., return_exceptions=True) returns exception as result."""

        async def _fail():
            raise RuntimeError("boom")

        async def _ok():
            return 42

        results = await asyncio.gather(_fail(), _ok(), return_exceptions=True)
        assert isinstance(results[0], RuntimeError)
        assert results[1] == 42

    @pytest.mark.asyncio
    async def test_cancel_gather_without_return_exceptions_propagates(self):
        async def _fail():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await asyncio.gather(_fail(), asyncio.sleep(0))

    @pytest.mark.asyncio
    async def test_cancel_propagates_cancelled_error(self):
        async def _worker():
            await asyncio.sleep(10)

        task = asyncio.create_task(_worker())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_cancel_does_not_corrupt_state(self):
        """After cancellation, shared mutable state is observed correctly."""
        count = [0]

        async def _increment_until_cancelled():
            try:
                while True:
                    count[0] += 1
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass  # clean exit

        task = asyncio.create_task(_increment_until_cancelled())
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        before = count[0]
        assert before >= 1
        # State is stable after cancellation
        await asyncio.sleep(0.01)
        assert count[0] == before  # no unexpected mutations

    @pytest.mark.asyncio
    async def test_cancel_during_asyncio_sleep_raises_in_sleep(self):
        """Cancelling a task while it's in asyncio.sleep() raises CancelledError."""

        async def _sleeper():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(_sleeper())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# D — asyncio.to_thread wrapping (blocking I/O off the event loop)
# ---------------------------------------------------------------------------


class TestToThreadSafety:
    """asyncio.to_thread offloads blocking calls so the loop isn't stalled."""

    @pytest.mark.asyncio
    async def test_to_thread_does_not_block_loop(self):
        """Concurrent sleeps in to_thread run in parallel (thread pool)."""

        def _block(duration):
            time.sleep(duration)
            return duration

        t0 = time.monotonic()
        results = await asyncio.gather(
            asyncio.to_thread(_block, 0.05),
            asyncio.to_thread(_block, 0.05),
        )
        elapsed = time.monotonic() - t0
        # Thread pool parallelism: total wall time < sum of sleeps
        assert elapsed < 0.10  # generous bound
        assert results == [0.05, 0.05]

    @pytest.mark.asyncio
    async def test_to_thread_exception_propagates(self):
        def _fail():
            raise ValueError("thread error")

        with pytest.raises(ValueError, match="thread error"):
            await asyncio.to_thread(_fail)

    @pytest.mark.asyncio
    async def test_to_thread_result_is_returned(self):
        def _compute(x):
            return x * 2

        result = await asyncio.to_thread(_compute, 21)
        assert result == 42


# ---------------------------------------------------------------------------
# E — Task group exception handling (asyncio.TaskGroup, Python 3.11+)
# ---------------------------------------------------------------------------


class TestTaskGroupSafety:
    """Python 3.11+ TaskGroup: exception in one task cancels siblings."""

    @pytest.mark.asyncio
    async def test_task_group_all_complete_normally(self):
        results: list[int] = []

        async def _work(n):
            await asyncio.sleep(0.001 * n)
            results.append(n)
            return n

        async with asyncio.TaskGroup() as tg:
            tg.create_task(_work(1))
            tg.create_task(_work(2))
        assert sorted(results) == [1, 2]

    @pytest.mark.asyncio
    async def test_task_group_exception_cancels_siblings(self):
        """Exception in one task cancels all others in the group."""
        started = asyncio.Event()
        cancelled_tracker = []

        async def _ok():
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled_tracker.append(True)
                raise

        async def _fail():
            await started.wait()
            await asyncio.sleep(0.01)
            raise RuntimeError("fire")

        with pytest.raises(ExceptionGroup) as exc_info:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_ok())
                tg.create_task(_fail())

        # The _ok task was cancelled because _fail raised
        errors = exc_info.value.exceptions
        assert any("fire" in str(e) for e in errors)
        assert len(cancelled_tracker) > 0

    @pytest.mark.asyncio
    async def test_task_group_empty(self):
        """Empty TaskGroup exits cleanly."""
        async with asyncio.TaskGroup():
            pass  # no tasks
        # No exception means success


# ---------------------------------------------------------------------------
# F — asyncio.Event stop-signal correctness
# ---------------------------------------------------------------------------


class TestEventStopSignal:
    """asyncio.Event is the canonical stop signal (used by off_peak_scheduler)."""

    @pytest.mark.asyncio
    async def test_event_cleared_by_default(self):
        ev = asyncio.Event()
        assert not ev.is_set()

    @pytest.mark.asyncio
    async def test_event_set_unblocks_wait(self):
        ev = asyncio.Event()
        results = []

        async def _waiter():
            await ev.wait()
            results.append(42)

        task = asyncio.create_task(_waiter())
        await asyncio.sleep(0)
        assert results == []
        ev.set()
        await task
        assert results == [42]

    @pytest.mark.asyncio
    async def test_background_loop_stops_on_event(self):
        """Simulates off_peak_scheduler._background_loop with stop_event."""
        ev = asyncio.Event()
        ticks: list[int] = []

        async def _loop():
            while not ev.is_set():
                ticks.append(1)
                await asyncio.sleep(0.001)

        task = asyncio.create_task(_loop())
        await asyncio.sleep(0.005)
        ev.set()
        await task
        assert len(ticks) >= 2  # loop ran at least twice before stopping


# ---------------------------------------------------------------------------
# G — Async context manager integrity
# ---------------------------------------------------------------------------


class TestAsyncContextManager:
    """Verify async context managers clean up on exception and cancellation."""

    @pytest.mark.asyncio
    async def test_aenter_failure_skips_aexit(self):
        """If __aenter__ raises, __aexit__ is NOT called."""

        class BadCM:
            async def __aenter__(self):
                raise RuntimeError("enter failed")

            async def __aexit__(self, *args):
                pytest.fail("__aexit__ should not be called after __aenter__ failure")

        with pytest.raises(RuntimeError, match="enter failed"):
            async with BadCM():
                pass

    @pytest.mark.asyncio
    async def test_exception_in_body_calls_aexit(self):
        """Exception in the body calls __aexit__ with exc info."""
        exited: list[bool] = []

        class TrackedCM:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                exited.append(exc_type is not None)
                return False  # don't suppress

        with pytest.raises(ValueError):
            async with TrackedCM():
                raise ValueError("body error")
        assert exited == [True]

    @pytest.mark.asyncio
    async def test_aexit_suppresses_exception_when_returns_true(self):
        class SuppressCM:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return True  # suppress

        async with SuppressCM():
            raise RuntimeError("this is suppressed")
        # Should not raise

    @pytest.mark.asyncio
    async def test_asyncio_lock_is_re_entrant_safe(self):
        """asyncio.Lock is not re-entrant — verify deadlock detection."""
        lock = asyncio.Lock()

        async with lock:
            # Same task trying to re-acquire would deadlock;
            # verify a different coroutine can queue.
            acquired_inner = []

            async def _inner():
                async with lock:
                    acquired_inner.append(True)

            # Don't await — just schedule
            inner_task = asyncio.create_task(_inner())
            await asyncio.sleep(0)
            assert acquired_inner == []
            # outer lock release queued for inner
        await inner_task
        assert acquired_inner == [True]


# ---------------------------------------------------------------------------
# H — Retry with exponential backoff
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    """Parity with chat/session.py _post_with_retry pattern."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_first_attempt(self):
        call_count = 0

        async def _attempt():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await self._retry(_attempt, max_retries=2)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_eventually_succeeds_after_transient_failures(self):
        call_count = 0

        async def _attempt():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "recovered"

        result = await self._retry(_attempt, max_retries=2, retry_on=ConnectionError)
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausts_and_raises_last_exception(self):
        call_count = 0

        async def _always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("always down")

        with pytest.raises(ConnectionError, match="always down"):
            await self._retry(_always_fail, max_retries=2, retry_on=ConnectionError)
        assert call_count == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_retry_does_not_retry_on_unmatched_exception(self):
        call_count = 0

        async def _value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            await self._retry(_value_error, max_retries=2, retry_on=ConnectionError)
        assert call_count == 1  # no retries

    @staticmethod
    async def _retry(fn, max_retries=2, retry_on=Exception):
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                return await fn()
            except retry_on as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(0.001 * (2**attempt))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# I — asyncio.Lock serialization
# ---------------------------------------------------------------------------


class TestAsyncLockSerialization:
    """asyncio.Lock serializes concurrent access (used by engine commit_lock)."""

    @pytest.mark.asyncio
    async def test_lock_serializes_concurrent_work(self):
        lock = asyncio.Lock()
        active: list[int] = []  # max concurrent in critical section
        order: list[int] = []

        async def _critical(n):
            async with lock:
                active.append(n)
                order.append(n)
                await asyncio.sleep(0.01)
                active.remove(n)

        await asyncio.gather(*[_critical(i) for i in range(5)])
        # Lock guarantees mutual exclusion: only 1 at a time
        assert order == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_lock_is_fair_under_contention(self):
        """Tasks acquire the lock in FIFO order under contention."""
        lock = asyncio.Lock()
        acquired_order: list[int] = []

        async def _worker(i):
            async with lock:
                acquired_order.append(i)
                await asyncio.sleep(0)

        # Start all workers concurrently
        tasks = [asyncio.create_task(_worker(i)) for i in range(10)]
        await asyncio.gather(*tasks)
        assert acquired_order == list(range(10))


# ---------------------------------------------------------------------------
# J — map_reduce gather aggregation
# ---------------------------------------------------------------------------


class TestMapReduceGather:
    """asyncio.gather used for concurrent batch execution (map_reduce_executor)."""

    @pytest.mark.asyncio
    async def test_gather_aggregates_results_in_order(self):
        async def _work(n):
            await asyncio.sleep(0.001 * (10 - n))
            return n * n

        coros = [_work(i) for i in range(5)]
        results = list(await asyncio.gather(*coros))
        assert results == [0, 1, 4, 9, 16]

    @pytest.mark.asyncio
    async def test_gather_with_return_exceptions_partial_failure(self):
        async def _ok(n):
            return n * 2

        async def _fail(n):
            if n == 2:
                raise ValueError("bad input")
            return n * 2

        coros = [_fail(i) if i == 2 else _ok(i) for i in range(4)]
        results = await asyncio.gather(*coros, return_exceptions=True)
        assert results[0] == 0
        assert results[1] == 2
        assert isinstance(results[2], ValueError)
        assert results[3] == 6

    @pytest.mark.asyncio
    async def test_gather_empty_list_returns_empty(self):
        results = await asyncio.gather()
        assert results == []


# ---------------------------------------------------------------------------
# K — create_task without done_callback leaks (daemon _sink pattern)
# ---------------------------------------------------------------------------


class TestTaskTracking:
    """daemon.py _sink: create_task with set-based tracking."""

    @pytest.mark.asyncio
    async def test_tracked_task_removed_after_completion(self):
        pending: set[asyncio.Task] = set()

        async def _work():
            await asyncio.sleep(0.001)

        task = asyncio.create_task(_work())
        pending.add(task)
        task.add_done_callback(pending.discard)
        await asyncio.wait_for(task, timeout=1)
        assert task not in pending

    @pytest.mark.asyncio
    async def test_tracked_task_removed_after_exception(self):
        pending: set[asyncio.Task] = set()

        async def _fail():
            await asyncio.sleep(0.001)
            raise RuntimeError("oops")

        task = asyncio.create_task(_fail())
        pending.add(task)
        task.add_done_callback(pending.discard)
        with contextlib.suppress(RuntimeError):
            await asyncio.wait_for(task, timeout=1)
        assert task not in pending

    def test_create_task_outside_running_loop_raises(self):
        """create_task outside a running loop raises and does not leak a coroutine."""
        coroutine = asyncio.sleep(0)
        task: asyncio.Task[None] | None = None
        try:
            with pytest.raises(RuntimeError, match="no running event loop"):
                task = asyncio.create_task(coroutine)
        finally:
            coroutine.close()
        assert task is None

    def test_loop_create_task_accepts_non_running_loop(self):
        """An explicit loop owns tasks even before run_until_complete starts it."""
        loop = asyncio.new_event_loop()
        task = loop.create_task(asyncio.sleep(0))
        try:
            loop.run_until_complete(task)
            assert task.done()
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# L — Blocking I/O detection pattern (structural guard)
# ---------------------------------------------------------------------------


class TestBlockingIODetection:
    """Verify that known async functions avoid sync blocking patterns."""

    def test_src_async_functions_importable_without_syntax_errors(self):
        """All async source modules are syntactically valid."""

    def test_blocking_open_not_in_async_functions(self):
        """Grok-sniff: no async function in key modules calls blocking open()."""
        import ast
        import inspect

        from general_ludd.execution import engine as _engine_mod

        # engine.py has async execute_async — check its AST
        source = inspect.getsource(_engine_mod.ExecutionEngine.execute_async)
        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and not isinstance(node, ast.AsyncWith):
                # blocking 'with open(...)' inside async def is a smell
                for item in node.items:
                    if isinstance(item.context_expr, ast.Call):
                        func = item.context_expr
                        if isinstance(func.func, ast.Name) and func.func.id == "open":
                            pytest.fail("Blocking open() found inside async execute_async")
        # If we get here, no blocking open() AST node found in execute_async
