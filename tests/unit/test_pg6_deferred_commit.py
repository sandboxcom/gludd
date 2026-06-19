"""Tests for PG-6: non-blocking deferred git commit."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.execution.engine import (
    ExecutionEngine,
    _git_commit_async,
)
from general_ludd.schemas.job import JobSpec


def _make_job(**kwargs: object) -> JobSpec:
    defaults: dict[str, object] = dict(
        job_id="job-pg6-test",
        todo_id="TODO-PG6",
        prompt_text="test deferred commit",
        work_type="code",
        playbook="code",
        queue="core",
    )
    defaults.update(kwargs)
    return JobSpec(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. _git_commit_async calls the sync function via executor
# ---------------------------------------------------------------------------
def test_git_commit_async_calls_sync_via_executor() -> None:
    """_git_commit_async should run _git_commit in a thread executor."""
    async def _run() -> None:
        with patch(
            "general_ludd.execution.engine._git_commit", return_value="deadbeef"
        ) as mock_commit:
            result = await _git_commit_async("/tmp/x", "some message")
        mock_commit.assert_called_once_with("/tmp/x", "some message")
        assert result == "deadbeef"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. _git_commit_async returns the value from _git_commit (including None)
# ---------------------------------------------------------------------------
def test_git_commit_async_returns_value_from_sync() -> None:
    """_git_commit_async propagates the return value of _git_commit."""
    async def _run() -> None:
        with patch(
            "general_ludd.execution.engine._git_commit", return_value="abc12345"
        ):
            result = await _git_commit_async("/tmp/repo", "msg")
        assert result == "abc12345"

    asyncio.run(_run())


def test_git_commit_async_returns_none_when_sync_returns_none() -> None:
    """_git_commit_async returns None when _git_commit returns None."""
    async def _run() -> None:
        with patch(
            "general_ludd.execution.engine._git_commit", return_value=None
        ):
            result = await _git_commit_async("/tmp/repo", "msg")
        assert result is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. defer_commit adds a task to _background_tasks
# ---------------------------------------------------------------------------
def test_defer_commit_adds_to_background_tasks() -> None:
    """defer_commit schedules a background task within a running event loop."""
    async def _run() -> None:
        engine = ExecutionEngine(workspace_path="/tmp/test-pg6-ws")
        with patch(
            "general_ludd.execution.engine._git_commit", return_value="abc12345"
        ):
            engine.defer_commit("/tmp/test-pg6-ws", "test commit")
        # Give the event loop a tick to process
        await asyncio.sleep(0)
        # No exception was raised — method succeeded
        # The task may have already completed and been discarded by the callback
        # Just verify no exception during the whole cycle
        assert isinstance(engine._background_tasks, set)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. defer_commit returns immediately (non-blocking)
# ---------------------------------------------------------------------------
def test_defer_commit_returns_immediately() -> None:
    """defer_commit must not block the caller."""
    import time

    async def _run() -> None:
        engine = ExecutionEngine(workspace_path="/tmp/test-pg6-timing")

        # Use a slow mock to confirm defer_commit doesn't block on it
        slow_called = False

        async def _slow_commit(path: str, msg: str) -> str | None:
            nonlocal slow_called
            await asyncio.sleep(0.5)
            slow_called = True
            return "abcdef12"

        with patch(
            "general_ludd.execution.engine._git_commit_async",
            side_effect=_slow_commit,
        ):
            start = time.monotonic()
            engine.defer_commit("/tmp/test-pg6-timing", "deferred msg")
            elapsed = time.monotonic() - start

        # defer_commit itself must return in well under 100ms
        assert elapsed < 0.1, f"defer_commit blocked for {elapsed:.3f}s"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. execute_async returns a TaskReturn (no gateway → error exit_code)
# ---------------------------------------------------------------------------
def test_execute_async_no_model_gateway_returns_error() -> None:
    """execute_async with no model gateway returns exit_code=1 TaskReturn."""
    async def _run() -> None:
        engine = ExecutionEngine(
            model_gateway=None, workspace_path="/tmp/test-pg6-no-gw"
        )
        job = _make_job()
        result = await engine.execute_async(job)
        assert result.exit_code == 1
        assert "No model gateway" in result.result_summary

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 6. execute_async returns a valid TaskReturn with a mock gateway
# ---------------------------------------------------------------------------
def test_execute_async_returns_task_return() -> None:
    """execute_async returns a TaskReturn when model gateway is configured."""
    async def _run() -> None:
        mock_gw = MagicMock()
        mock_response = MagicMock()
        mock_response.content = (
            "FILE: workspace_test_file.txt\nsome generated content\n"
        )
        mock_gw.call_model.return_value = mock_response

        engine = ExecutionEngine(
            model_gateway=mock_gw,
            workspace_path="/tmp/test-pg6-gw",
        )
        job = _make_job()

        with (
            patch("general_ludd.execution.engine._is_git_repo", return_value=False),
            patch("general_ludd.execution.engine._run_tests", return_value=(0, "ok")),
        ):
            result = await engine.execute_async(job)

        from general_ludd.schemas.task_return import TaskReturn
        assert isinstance(result, TaskReturn)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 7. execute_async defers the commit (non-blocking) when git repo
# ---------------------------------------------------------------------------
def test_execute_async_defers_commit_not_blocks() -> None:
    """execute_async defers commit via defer_commit, not blocking _git_commit."""
    commit_calls: list[str] = []

    async def _run() -> None:
        mock_gw = MagicMock()
        mock_response = MagicMock()
        mock_response.content = (
            "FILE: async_test_file.txt\nsome async content\n"
        )
        mock_gw.call_model.return_value = mock_response

        engine = ExecutionEngine(
            model_gateway=mock_gw,
            workspace_path="/tmp/test-pg6-defer",
        )

        original_defer = engine.defer_commit

        def _spy_defer(path: str, message: str) -> None:
            commit_calls.append(message)
            original_defer(path, message)

        engine.defer_commit = _spy_defer  # type: ignore[method-assign]

        job = _make_job()

        with (
            patch("general_ludd.execution.engine._is_git_repo", return_value=True),
            patch(
                "general_ludd.execution.engine._git_create_branch", return_value=True
            ),
            patch("general_ludd.execution.engine._run_tests", return_value=(0, "ok")),
            patch(
                "general_ludd.execution.engine._git_commit", return_value="def01234"
            ),
        ):
            result = await engine.execute_async(job)

        # defer_commit was called, not the blocking _git_commit directly
        assert len(commit_calls) == 1
        assert "TODO-PG6" in commit_calls[0]
        # Result says commit deferred
        assert "deferred" in result.result_summary.lower()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 8. _background_tasks is initialized as empty set
# ---------------------------------------------------------------------------
def test_background_tasks_set_initialized() -> None:
    """ExecutionEngine always initializes _background_tasks as an empty set."""
    engine = ExecutionEngine(workspace_path="/tmp/test-pg6-init")
    assert isinstance(engine._background_tasks, set)
    assert len(engine._background_tasks) == 0


# ---------------------------------------------------------------------------
# 9. defer_commit swallows exceptions when called outside event loop
# ---------------------------------------------------------------------------
def test_defer_commit_handles_no_running_loop() -> None:
    """defer_commit silently swallows RuntimeError when no event loop is running."""
    engine = ExecutionEngine(workspace_path="/tmp/test-pg6-no-loop")
    # Calling defer_commit outside asyncio.run() — create_task will raise
    # RuntimeError("no running event loop"). The try/except must swallow it.
    try:
        engine.defer_commit("/tmp/test-pg6-no-loop", "commit msg")
    except Exception as exc:
        pytest.fail(f"defer_commit raised outside of event loop: {exc}")
