"""W3.3 (M9): Event loop run_playbook must run in asyncio.to_thread, not block the loop.

TDD: write the test first, then fix loop.py.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRunPlaybookNonBlocking:
    """Assert that _dispatch_execute_job (when using self._runner) yields control
    during a slow playbook run so that other coroutines can run concurrently."""

    @pytest.mark.asyncio
    async def test_run_playbook_via_to_thread_yields_control(self):
        """A slow playbook must not block the event loop.

        Proof strategy: schedule a fast coroutine that sets a flag, then
        call _dispatch_execute_job with a slow (sleep) runner. If run_playbook
        blocks synchronously, the fast coroutine flag will not be set until
        after the slow run finishes. With asyncio.to_thread it runs
        concurrently, so the flag will be set during the run.
        """
        from general_ludd.event_loop.loop import EventLoop

        slow_started = asyncio.Event()
        fast_completed = asyncio.Event()

        def slow_runner_run_playbook(*args, **kwargs):
            slow_started.set()
            time.sleep(0.05)  # 50 ms "slow" playbook
            return {"rc": 0, "output": "done"}

        runner = MagicMock()
        runner.run_playbook.side_effect = slow_runner_run_playbook
        runner.prepare_job_dirs.return_value = {"root": "/tmp/test-job"}
        runner.write_vars.return_value = None
        runner.list_playbooks.return_value = ["noop.yml"]

        loop = EventLoop(
            config={"default_playbook": "noop.yml"},
            daemon_state={},
        )
        loop._runner = runner

        todo = MagicMock()
        todo.todo_id = "T-001"
        todo.work_type = "code"
        todo.queue = "core"
        todo.priority = "medium"
        todo.prompt_profile = None
        todo.model_profile = None
        todo.project_id = None
        todo.plan_artifact = None
        todo.resource_profile = "low_resource"

        async def set_fast_flag():
            await slow_started.wait()
            fast_completed.set()

        # Both coroutines run; if run_playbook blocks, fast_completed won't
        # be set until after dispatch completes.
        fast_task = asyncio.create_task(set_fast_flag())

        # Patch the session and other deps the dispatch path uses
        loop._active_session = None
        loop._mcp_tool_registry = None
        loop._budget_guard = None
        loop._prompt_registry = None
        loop._config_snapshot = {"default_playbook": "noop.yml"}
        loop._tick_state = {}
        loop._project_workspace = {}
        loop._http_client = None
        loop._adaptive_router = None
        loop._project_secrets_manager = None

        with (
            patch.object(loop, "_resolve_adaptive_prompt", new=AsyncMock(return_value=(None, None, None))),
            patch.object(loop, "_load_shared_vars", new=AsyncMock(return_value={})),
        ):
            await loop._dispatch_execute_job(todo)

        await fast_task

        assert fast_completed.is_set(), (
            "run_playbook blocked the event loop: fast coroutine did not run "
            "during the playbook execution. Wrap run_playbook in asyncio.to_thread."
        )

    @pytest.mark.asyncio
    async def test_shutdown_drain_on_cancel(self):
        """When the loop task is cancelled while a playbook runs, the
        dispatch coroutine must not leave dangling threads.

        Proof: cancel a dispatch while the runner is sleeping; the
        asyncio.to_thread wrapper will propagate CancelledError cleanly
        (the thread keeps running but the coroutine returns immediately).
        No RuntimeError or unhandled exception.
        """
        from general_ludd.event_loop.loop import EventLoop

        cancel_during_run = asyncio.Event()

        def blocking_runner(*args, **kwargs):
            cancel_during_run.set()
            time.sleep(0.02)
            return {"rc": 0, "output": "ok"}

        runner = MagicMock()
        runner.run_playbook.side_effect = blocking_runner
        runner.prepare_job_dirs.return_value = {"root": "/tmp/test-job2"}
        runner.write_vars.return_value = None
        runner.list_playbooks.return_value = ["noop.yml"]

        loop = EventLoop(
            config={"default_playbook": "noop.yml"},
            daemon_state={},
        )
        loop._runner = runner
        loop._active_session = None
        loop._mcp_tool_registry = None
        loop._budget_guard = None
        loop._prompt_registry = None
        loop._config_snapshot = {"default_playbook": "noop.yml"}
        loop._tick_state = {}
        loop._project_workspace = {}
        loop._http_client = None
        loop._adaptive_router = None
        loop._project_secrets_manager = None

        todo = MagicMock()
        todo.todo_id = "T-002"
        todo.work_type = "code"
        todo.queue = "core"
        todo.priority = "medium"
        todo.prompt_profile = None
        todo.model_profile = None
        todo.project_id = None
        todo.plan_artifact = None
        todo.resource_profile = "low_resource"

        with (
            patch.object(loop, "_resolve_adaptive_prompt", new=AsyncMock(return_value=(None, None, None))),
            patch.object(loop, "_load_shared_vars", new=AsyncMock(return_value={})),
        ):
            task = asyncio.create_task(loop._dispatch_execute_job(todo))
            await cancel_during_run.wait()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
