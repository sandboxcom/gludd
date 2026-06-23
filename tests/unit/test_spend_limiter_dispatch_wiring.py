"""TDD: wire SpendLimiter.would_exceed() into EventLoop._dispatch_execute_job.

The SpendLimiter is built in daemon.py and exposed on app.state._spend_limiter,
and make_spend_guarded_executor wraps the gateway executor — but the EventLoop's
per-job dispatch path never consults would_exceed(). These tests drive the wiring:

  1. over budget  -> dispatch is SKIPPED (runner.run_playbook NOT called), warn logged
  2. under budget -> dispatch proceeds (runner.run_playbook called)
  3. no limiter   -> dispatch proceeds (backward compatible)
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_loop(spend_limiter: object | None = None) -> MagicMock:
    from general_ludd.event_loop.loop import EventLoop

    loop = EventLoop(
        config={"default_playbook": "noop.yml", "budget": {"per_dispatch_usd": 0.05}},
        daemon_state={},
        spend_limiter=spend_limiter,
    )
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
    loop._model_gateway = None
    return loop


def _make_todo() -> MagicMock:
    todo = MagicMock()
    todo.todo_id = "T-spend-1"
    todo.work_type = "code"
    todo.queue = "core"
    todo.priority = "medium"
    todo.prompt_profile = None
    todo.model_profile = None
    todo.project_id = None
    todo.plan_artifact = None
    todo.resource_profile = "low_resource"
    return todo


def _make_runner() -> MagicMock:
    runner = MagicMock()
    runner.run_playbook.return_value = {"rc": 0, "output": "done"}
    runner.prepare_job_dirs.return_value = {"root": "/tmp/test-job"}
    runner.write_vars.return_value = None
    return runner


class TestSpendLimiterDispatchGate:
    @pytest.mark.asyncio
    async def test_over_budget_skips_dispatch(self, caplog: pytest.LogCaptureFixture) -> None:
        """When would_exceed() is True, the job is NOT dispatched and a warning is logged."""
        limiter = MagicMock()
        limiter.would_exceed.return_value = True
        limiter.window_spend.return_value = 0.10
        limiter.remaining.return_value = 0.0

        loop = _make_loop(spend_limiter=limiter)
        loop._runner = _make_runner()

        with (
            patch.object(loop, "_resolve_adaptive_prompt", new=AsyncMock(return_value=(None, None, None))),
            patch.object(loop, "_load_shared_vars", new=AsyncMock(return_value={})),
            caplog.at_level(logging.WARNING),
        ):
            await loop._dispatch_execute_job(_make_todo())

        assert not loop._runner.run_playbook.called, (
            "Over-budget dispatch must NOT reach run_playbook."
        )
        limiter.would_exceed.assert_called_once()
        assert any("spend" in rec.message.lower() or "deferring" in rec.message.lower() for rec in caplog.records), (
            "An over-budget dispatch must log a warning."
        )

    @pytest.mark.asyncio
    async def test_under_budget_proceeds(self) -> None:
        """When would_exceed() is False, the job dispatches normally."""
        limiter = MagicMock()
        limiter.would_exceed.return_value = False

        loop = _make_loop(spend_limiter=limiter)
        loop._runner = _make_runner()

        with (
            patch.object(loop, "_resolve_adaptive_prompt", new=AsyncMock(return_value=(None, None, None))),
            patch.object(loop, "_load_shared_vars", new=AsyncMock(return_value={})),
        ):
            await loop._dispatch_execute_job(_make_todo())

        assert loop._runner.run_playbook.called, (
            "Under-budget dispatch must reach run_playbook."
        )
        limiter.would_exceed.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_limiter_proceeds(self) -> None:
        """With no spend_limiter wired, dispatch is unchanged (backward compatible)."""
        loop = _make_loop(spend_limiter=None)
        loop._runner = _make_runner()

        with (
            patch.object(loop, "_resolve_adaptive_prompt", new=AsyncMock(return_value=(None, None, None))),
            patch.object(loop, "_load_shared_vars", new=AsyncMock(return_value={})),
        ):
            await loop._dispatch_execute_job(_make_todo())

        assert loop._runner.run_playbook.called, (
            "Absence of a spend_limiter must not change dispatch behaviour."
        )
