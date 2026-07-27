"""S1: Noop executor must no longer silently complete tasks — it must fail-loud.

Ensures the _noop_executor path (used when no model_gateway is configured)
returns status="failed" instead of status="completed" with empty output.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from general_ludd.agents.dispatcher import (
    AgentDispatcher,
    AgentTask,
    DispatchStatus,
    _noop_executor,
)
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType


def _register_invoker(reg: AgentRegistry, name: str = "build") -> str:
    reg.register(
        AgentConfig(
            name=name,
            type=AgentType.PRIMARY,
            description="d",
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
        )
    )
    return name


class TestS1NoopExecutorFailLoud:
    def test_noop_executor_logs_warning(self, caplog):
        """The noop executor must emit a WARNING log line — never silent."""
        task = AgentTask(
            task_id="t1",
            agent_name="c",
            description="d",
            prompt="p",
        )
        with caplog.at_level(logging.WARNING, logger="general_ludd.agents.dispatcher"):
            result = asyncio.run(_noop_executor(task))

        assert result == "__NOOP_EXECUTOR_UNCONFIGURED__"
        assert len(caplog.records) >= 1
        assert any("noop" in r.message.lower() or "unconfigured" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_dispatcher_falls_back_to_failed_not_completed(self):
        """No-model dispatcher must return status='failed', not 'completed'."""
        reg = AgentRegistry()
        reg.register(AgentConfig(name="g", type=AgentType.SUBAGENT, description="d"))
        invoker = _register_invoker(reg)
        d = AgentDispatcher(registry=reg)
        r = await d.dispatch_one(
            AgentTask(
                task_id="t4",
                agent_name="g",
                description="d",
                prompt="p",
                invoker_name=invoker,
            )
        )
        # S1 fix: was "completed", must now be "failed"
        assert r.status == "failed", f"Expected 'failed', got {r.status!r}"
        assert "unconfigured" in r.output.lower() or "noop" in r.output.lower()

    @pytest.mark.asyncio
    async def test_dispatcher_with_real_executor_still_returns_completed(self):
        """Real executor must still return 'completed' — S1 must not regress real path."""
        reg = AgentRegistry()
        reg.register(AgentConfig(name="g", type=AgentType.SUBAGENT, description="d"))
        invoker = _register_invoker(reg)

        async def real_executor(task: AgentTask) -> str:
            return "real output"

        d = AgentDispatcher(registry=reg, executor=real_executor)
        r = await d.dispatch_one(
            AgentTask(
                task_id="t5",
                agent_name="g",
                description="d",
                prompt="p",
                invoker_name=invoker,
            )
        )
        assert r.status == "completed"
        assert r.output == "real output"

    @pytest.mark.asyncio
    async def test_dispatch_status_eq_maintains_backcompat(self):
        """DispatchStatus __eq__ must treat 'success' == 'completed'."""
        assert DispatchStatus("completed") == "completed"
        assert DispatchStatus("completed") == "success"
        assert DispatchStatus("success") == "completed"
        assert DispatchStatus("failed") != "completed"
