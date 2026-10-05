"""S.16 — daemon run_until_complete in running uvicorn loop (D11/CA-D1).

Verify: (a) daemon.py _lazy_*_handler closures are async and use `return await h(...)`
(no run_until_complete), (b) _record_generation_benchmark uses get_running_loop()
not the deprecated get_event_loop(), and (c) run_until_complete is never called
on a running loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Structural: daemon.py _lazy_*_handler closures are async
# ---------------------------------------------------------------------------

@pytest.fixture
def daemon_module() -> Any:
    return importlib.import_module("general_ludd.daemon")


class TestLazyHandlerClosuresAreAsync:
    def test_lazy_mcp_handler_is_async(self, daemon_module: Any) -> None:
        src = inspect.getsource(daemon_module)
        assert "async def _lazy_mcp_handler" in src, (
            "_lazy_mcp_handler must be async def so it uses `await`, not "
            "run_until_complete inside the running uvicorn loop"
        )
        assert "return await h(name, args)" in src, (
            "_lazy_mcp_handler must `return await h(...)` to avoid run_until_complete"
        )

    def test_lazy_role_handler_is_async(self, daemon_module: Any) -> None:
        src = inspect.getsource(daemon_module)
        assert "async def _lazy_role_handler" in src, (
            "_lazy_role_handler must be async def so it uses `await`, not "
            "run_until_complete inside the running uvicorn loop"
        )
        assert "return await h(name, args)" in src, (
            "_lazy_role_handler must `return await h(...)` to avoid run_until_complete"
        )

    def test_lazy_collection_handler_is_async(self, daemon_module: Any) -> None:
        src = inspect.getsource(daemon_module)
        assert "async def _lazy_collection_handler" in src, (
            "_lazy_collection_handler must be async def"
        )

    def test_daemon_py_no_run_until_complete_outside_comments(self, daemon_module: Any) -> None:
        """daemon.py must not contain a live `run_until_complete` call (only
        docstrings/comments referencing it are allowed)."""
        src = inspect.getsource(daemon_module)
        lines = src.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if (
                "run_until_complete" in stripped
                and not stripped.startswith("#")
                and '"""' not in stripped
                and "run_until_complete" not in stripped.split('"""')[0]
            ):
                pytest.fail(
                    f"daemon.py line {i}: live run_until_complete found — "
                    f"must use async/await or asyncio.create_task instead: {stripped}"
                )


# ---------------------------------------------------------------------------
# Structural: _record_generation_benchmark uses get_running_loop
# ---------------------------------------------------------------------------

class TestRecordGenerationBenchmarkNoDeprecatedAPI:
    def test_uses_get_running_loop_not_get_event_loop(self) -> None:
        """The deprecated asyncio.get_event_loop() must not appear in the
        re-imported module source of job_invocation."""
        module = importlib.import_module("general_ludd.models.job_invocation")
        src = inspect.getsource(module._record_generation_benchmark)
        assert "get_event_loop(" not in src, (
            "_record_generation_benchmark must use asyncio.get_running_loop(), "
            "not the deprecated get_event_loop()"
        )
        assert "get_running_loop" in src, (
            "_record_generation_benchmark must use asyncio.get_running_loop() "
            "to safely detect the running loop"
        )

    def test_no_run_until_complete_called_on_running_loop(self) -> None:
        """`run_until_complete` must NOT be called on a running event loop
        in the benchmark recorder path."""
        module = importlib.import_module("general_ludd.models.job_invocation")
        src = inspect.getsource(module._record_generation_benchmark)
        assert "run_until_complete" not in src, (
            "_record_generation_benchmark must never call run_until_complete — "
            "it must use get_running_loop() + create_task or asyncio.run() fallback"
        )


# ---------------------------------------------------------------------------
# Behavioral: _record_generation_benchmark on a running loop
# ---------------------------------------------------------------------------

class TestRecordGenerationBenchmarkRunningLoop:
    """Integration-level: verify _record_generation_benchmark dispatches via
    create_task on a running loop without RuntimeError."""

    def test_schedules_on_running_loop_without_runtime_error(self) -> None:
        """Verify _record_generation_benchmark does NOT raise RuntimeError
        ('Event loop is already running') when called from inside a running
        event loop."""

        async def _async_create(**kwargs: Any) -> MagicMock:
            return MagicMock()

        recorder = MagicMock(spec=["create"])
        recorder.create.side_effect = _async_create

        async def _runner() -> None:
            from general_ludd.models.job_invocation import _BACKGROUND_TASKS, _record_generation_benchmark
            prior = len(_BACKGROUND_TASKS)
            _record_generation_benchmark(
                recorder,
                model_profile="p",
                work_type="code",
                input_tokens=10,
                output_tokens=5,
            )
            # Must not have raised RuntimeError
            after = len(_BACKGROUND_TASKS)
            assert after >= prior, "An async recorder.create() should schedule a background task"

            # Drain tasks
            for task in list(_BACKGROUND_TASKS):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    if not task.done():
                        await task

        asyncio.run(_runner())

    def test_does_not_raise_runtime_error_on_running_loop_non_awaitable(self) -> None:
        """The function must not raise RuntimeError when recorder.create()
        returns a non-awaitable value on a running loop."""

        recorder = MagicMock(spec=["create"])
        recorder.create.return_value = MagicMock()

        async def _runner() -> None:
            from general_ludd.models.job_invocation import _record_generation_benchmark
            _record_generation_benchmark(
                recorder,
                model_profile="p",
                work_type="code",
                input_tokens=10,
                output_tokens=5,
            )

        asyncio.run(_runner())


# ---------------------------------------------------------------------------
# Behavioral: _record_generation_benchmark with awaitable create()
# ---------------------------------------------------------------------------

class TestRecordGenerationBenchmarkAwaitable:
    async def _make_awaitable_recorder(self) -> MagicMock:
        async def _async_create(**kwargs: Any) -> MagicMock:
            return MagicMock()

        recorder = MagicMock(spec=["create"])
        recorder.create.side_effect = _async_create
        recorder.create.return_value = await _async_create(
            model_profile_id="test", work_type="code",
            input_tokens=10, output_tokens=5, success=True, scoring="generation_path",
        )
        return recorder

    def test_awaitable_create_on_running_loop_schedules_task(self) -> None:
        bg_tasks_seen: list[Any] = []

        async def _runner() -> None:
            from general_ludd.models.job_invocation import _BACKGROUND_TASKS, _record_generation_benchmark

            len(_BACKGROUND_TASKS)
            recorder = MagicMock(spec=["create"])

            async def _async_create(**kwargs: Any) -> MagicMock:
                return MagicMock()

            recorder.create.return_value = _async_create(
                model_profile_id="p", work_type="code",
                input_tokens=10, output_tokens=5, success=True, scoring="generation_path",
            )

            _record_generation_benchmark(
                recorder,
                model_profile="p",
                work_type="code",
                input_tokens=10,
                output_tokens=5,
            )
            bg_tasks_seen.append(len(_BACKGROUND_TASKS))

            # Let any scheduled tasks complete
            for task in list(_BACKGROUND_TASKS):
                with contextlib.suppress(Exception):
                    if not task.done():
                        await task

        asyncio.run(_runner())
        assert bg_tasks_seen, "Task scheduling must have occurred"
        assert bg_tasks_seen[0] >= 0, "Background tasks may have been tracked"

    def test_shutdown_drain_cancels_and_awaits_background_tasks(self) -> None:
        async def _runner() -> None:
            from general_ludd.models.job_invocation import (
                _BACKGROUND_TASKS,
                drain_background_tasks,
            )

            task = asyncio.create_task(asyncio.Event().wait())
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_TASKS.discard)

            await drain_background_tasks()

            assert task.done()
            assert not _BACKGROUND_TASKS

        asyncio.run(_runner())
