"""C10 — Execution engine fixes: benchmark task errors surfaced,
_run_tests non-blocking via asyncio.to_thread, deferred-commit race prevention,
_background_tasks drained on shutdown.

Four fixes from AGENTIC_IMPLEMENTATION_SPEC.md C10:
  1. benchmark create_task errors surfaced (not swallowed)
  2. _run_tests uses asyncio.to_thread (not blocking event loop)
  3. deferred-commit race prevented (concurrent commits serialized)
  4. _background_tasks drained on shutdown
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock, patch

from general_ludd.execution.engine import ExecutionEngine
from general_ludd.schemas.job import JobSpec


def _make_job(**kwargs: object) -> JobSpec:
    defaults: dict[str, object] = dict(
        job_id="job-c10-test",
        todo_id="TODO-C10",
        prompt_text="C10 test job",
        work_type="code",
        playbook="code",
        queue="core",
    )
    defaults.update(kwargs)
    return JobSpec(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fix 1: benchmark task errors surfaced (not swallowed)
# ---------------------------------------------------------------------------

def test_benchmark_create_task_errors_surfaced() -> None:
    """When a benchmark background task raises, the exception must be logged
    (not silently swallowed via bare ``except Exception: pass``).

    Pre-fix: the engine wraps benchmark task creation in a bare ``try/except``
    that catches RuntimeError (no running loop) but also silently discards
    every other exception, including the task's own failures. Post-fix: the
    done-callback logs task exceptions at ERROR, and RuntimeError is caught
    explicitly.
    """

    async def _run() -> None:
        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(
            content="FILE: test_c10.py\nx=1\n"
        )

        engine = ExecutionEngine(
            model_gateway=mock_gw,
            workspace_path="/tmp/test-c10-benchmark",
        )
        job = _make_job()

        # Set a benchmark recorder that exists so the engine enters the
        # benchmark path, but patch the imported coroutine to raise so
        # the task fails after create_task succeeds.
        engine._benchmark_recorder = MagicMock()

        engine_logger = logging.getLogger("general_ludd.execution.engine")

        async def _failing_benchmark(*args: Any, **kwargs: Any) -> None:
            raise ValueError("benchmark explosion")

        # Patch the benchmark module BEFORE the engine's lazy import triggers.
        with (
            patch(
                "general_ludd.event_loop.benchmark.record_job_benchmark",
                side_effect=_failing_benchmark,
            ),
            patch(
                "general_ludd.execution.engine._run_tests",
                return_value=(0, "ok"),
            ),
            patch(
                "general_ludd.execution.engine._is_git_repo",
                return_value=False,
            ),
            patch.object(engine_logger, "error", wraps=engine_logger.error) as log_error,
        ):
            result = await engine.execute_async(job)
            assert result.exit_code == 0

            # Let background tasks run to completion — the done_callback
            # fires after the task finishes.
            await asyncio.sleep(0.2)

            # The done-callback must log the exception at ERROR level.
            error_calls = [
                c for c in log_error.call_args_list
                if "benchmark" in str(c.args).lower()
                or "background task failed" in str(c.args).lower()
            ]
            assert len(error_calls) >= 1, (
                f"benchmark task exception was not logged — "
                f"create_task errors are still silently swallowed. "
                f"Error calls: {[c.args for c in log_error.call_args_list]}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Fix 2: _run_tests uses asyncio.to_thread in execute_async
# ---------------------------------------------------------------------------

def test_run_tests_not_blocking_event_loop() -> None:
    """execute_async must call _run_tests via asyncio.to_thread so the
    event loop is never blocked by a synchronous subprocess call.

    Pre-fix: _run_tests is called directly (synchronous, blocking).
    Post-fix: _run_tests is dispatched via asyncio.to_thread.

    Strategy: wrap asyncio.to_thread with a spy that records calls
    and delegates to the REAL implementation, so the model gateway
    call at line 492 still works correctly.
    """
    import asyncio as asyncio_mod

    spy_calls: list[tuple[object, tuple[object, ...]]] = []
    _real_to_thread = asyncio_mod.to_thread

    async def _spy_to_thread(fn: object, *args: object, **kwargs: object) -> Any:
        spy_calls.append((fn, args))
        return await _real_to_thread(fn, *args, **kwargs)  # type: ignore[arg-type]

    mock_run_tests = MagicMock(return_value=(0, "ok"))

    async def _run() -> None:
        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(
            content="FILE: test_c10_async.py\nprint('ok')\n"
        )

        engine = ExecutionEngine(
            model_gateway=mock_gw,
            workspace_path="/tmp/test-c10-async-tests",
        )
        job = _make_job()

        with (
            patch(
                "general_ludd.execution.engine._run_tests", mock_run_tests,
            ),
            patch(
                "general_ludd.execution.engine._is_git_repo",
                return_value=False,
            ),
            patch(
                "general_ludd.execution.engine.asyncio.to_thread",
                side_effect=_spy_to_thread,
            ),
        ):
            result = await engine.execute_async(job)
            assert result.exit_code == 0

    asyncio.run(_run())

    run_tests_calls = [
        (fn, args) for fn, args in spy_calls
        if fn is mock_run_tests
    ]
    assert len(run_tests_calls) >= 1, (
        f"_run_tests was not dispatched via asyncio.to_thread. "
        f"to_thread was called {len(spy_calls)} time(s) but "
        f"none were for _run_tests."
    )


# ---------------------------------------------------------------------------
# Fix 3: deferred-commit race prevention
# ---------------------------------------------------------------------------

def test_deferred_commit_no_race() -> None:
    """Concurrent deferred commits must serialize via an asyncio.Lock
    so git index operations never race."""

    call_count = 0
    concurrent_count = 0
    max_concurrent = 0

    async def _run() -> None:
        nonlocal call_count, concurrent_count, max_concurrent

        engine = ExecutionEngine(workspace_path="/tmp/test-c10-commit-race")

        async def _tracked_commit(path: str, msg: str) -> str | None:
            nonlocal call_count, concurrent_count, max_concurrent
            call_count += 1
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return "abcdef12"

        # Patch _git_commit_async at the module level.
        # _commit_with_lock inside defer_commit resolves _git_commit_async
        # from the MODULE globals, so patching the module works.
        import general_ludd.execution.engine as _eng
        original = _eng._git_commit_async
        _eng._git_commit_async = _tracked_commit  # type: ignore[assignment]
        try:
            engine.defer_commit("/tmp/test-c10-commit-race", "commit 1")
            engine.defer_commit("/tmp/test-c10-commit-race", "commit 2")

            await asyncio.sleep(0.5)

            assert call_count == 2, (
                f"Expected 2 commit calls, got {call_count}."
            )
            assert max_concurrent == 1, (
                f"deferred commits raced: max_concurrent={max_concurrent}"
            )
        finally:
            _eng._git_commit_async = original

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Fix 4: _background_tasks drained on shutdown
# ---------------------------------------------------------------------------

def test_background_tasks_drained_on_shutdown() -> None:
    """Engine.shutdown() must cancel and await all pending background tasks,
    leaving _background_tasks empty."""

    async def _run() -> None:
        engine = ExecutionEngine(workspace_path="/tmp/test-c10-shutdown")

        # Schedule a slow background commit.
        async def _slow_commit(path: str, msg: str) -> str | None:
            await asyncio.sleep(10.0)  # long enough to survive until shutdown
            return "deadbeef"

        with patch(
            "general_ludd.execution.engine._git_commit_async",
            side_effect=_slow_commit,
        ):
            engine.defer_commit("/tmp/test-c10-shutdown", "pending commit")

        # Task should be registered in _background_tasks.
        assert len(engine._background_tasks) >= 1, (
            "defer_commit did not register a background task"
        )

        # Shutdown: cancel + await all pending tasks.
        await engine.shutdown()

        # After shutdown, background_tasks must be empty.
        assert len(engine._background_tasks) == 0, (
            "shutdown() did not drain _background_tasks"
        )

    asyncio.run(_run())


def test_background_tasks_drained_on_shutdown_noop_when_empty() -> None:
    """Engine.shutdown() on an engine with no background tasks is a no-op."""

    async def _run() -> None:
        engine = ExecutionEngine(workspace_path="/tmp/test-c10-shutdown-empty")
        await engine.shutdown()
        assert len(engine._background_tasks) == 0

    asyncio.run(_run())


def test_shutdown_gracefully_completes_ready_commit() -> None:
    """Normal shutdown must not cancel a ready repository commit."""
    completed = False

    async def _run() -> None:
        nonlocal completed
        engine = ExecutionEngine(workspace_path="/tmp/test-c10-shutdown-ready")

        async def _ready_commit(path: str, msg: str) -> str | None:
            nonlocal completed
            await asyncio.sleep(0)
            completed = True
            return "deadbeef"

        with patch(
            "general_ludd.execution.engine._git_commit_async",
            side_effect=_ready_commit,
        ):
            engine.defer_commit("/tmp/test-c10-shutdown-ready", "ready commit")
            await engine.shutdown()

    asyncio.run(_run())
    assert completed
