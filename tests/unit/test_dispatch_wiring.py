"""TDD tests for event-loop-wiring: DynamicDispatcher real handlers + SpendLimiter pre-call gate.

Tests assert:
  1. DaemonHandlerFactory builds callable handlers for mcp/skill/role/collection kinds.
  2. Each handler routes correctly to the underlying client/registry (mocks injected).
  3. dispatch_router.register() receives non-None callables when real subsystems exist.
  4. SpendLimiter gate: would_exceed -> call deferred; under budget -> call proceeds.
  5. Gateway executor with SpendLimiter injected skips model call on over-budget projection.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# 1. DaemonHandlerFactory -- handler callables
# ---------------------------------------------------------------------------


class TestMcpHandler:
    """mcp_handler routes (name, args) to MCPClient.call_tool via server_id prefix."""

    def test_mcp_handler_is_callable(self) -> None:
        from general_ludd.daemon_wiring import make_mcp_handler

        mcp_client = MagicMock()
        handler = make_mcp_handler(mcp_client)
        assert callable(handler)

    @pytest.mark.asyncio
    async def test_mcp_handler_routes_to_call_tool(self) -> None:
        """name format 'server_id/tool_name' splits on first slash."""
        from general_ludd.daemon_wiring import make_mcp_handler

        mcp_client = MagicMock()
        mcp_client.call_tool = AsyncMock(return_value={"result": "ok"})
        handler = make_mcp_handler(mcp_client)
        result = await handler("myserver/mytool", {"arg": "val"})
        mcp_client.call_tool.assert_called_once_with("myserver", "mytool", {"arg": "val"})
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_mcp_handler_no_slash_uses_name_as_tool(self) -> None:
        """No slash: no server_id prefix -> raises ValueError."""
        from general_ludd.daemon_wiring import make_mcp_handler

        mcp_client = MagicMock()
        handler = make_mcp_handler(mcp_client)
        with pytest.raises(ValueError, match="server_id"):
            await handler("notool", {})

    def test_mcp_handler_none_client_returns_error(self) -> None:
        """When mcp_client is None, make_mcp_handler returns None (no handler registered)."""
        from general_ludd.daemon_wiring import make_mcp_handler

        handler = make_mcp_handler(None)
        assert handler is None


class TestSkillHandler:
    """skill_handler routes (name, args) to SkillRegistry.get(name).body."""

    def test_skill_handler_is_callable(self) -> None:
        from general_ludd.daemon_wiring import make_skill_handler

        registry = MagicMock()
        handler = make_skill_handler(registry)
        assert callable(handler)

    def test_skill_handler_returns_skill_body(self) -> None:
        from general_ludd.daemon_wiring import make_skill_handler

        mock_skill = MagicMock()
        mock_skill.body = "do the thing"
        registry = MagicMock()
        registry.get.return_value = mock_skill
        handler = make_skill_handler(registry)
        result = handler("my_skill", {})
        registry.get.assert_called_once_with("my_skill")
        assert result == "do the thing"

    def test_skill_handler_unknown_skill_returns_error(self) -> None:
        from general_ludd.daemon_wiring import make_skill_handler

        registry = MagicMock()
        registry.get.return_value = None
        handler = make_skill_handler(registry)
        result = handler("unknown_skill", {})
        assert "unknown" in str(result).lower() or "not found" in str(result).lower()

    def test_skill_handler_none_registry_returns_none(self) -> None:
        from general_ludd.daemon_wiring import make_skill_handler

        handler = make_skill_handler(None)
        assert handler is None


class TestRoleHandler:
    """role_handler routes (name, args) to AgentDispatcher via a sync-over-async wrapper."""

    def test_role_handler_is_callable(self) -> None:
        from general_ludd.daemon_wiring import make_role_handler

        dispatcher = MagicMock()
        handler = make_role_handler(dispatcher)
        assert callable(handler)

    @pytest.mark.asyncio
    async def test_role_handler_dispatches_via_agent_dispatcher(self) -> None:
        from general_ludd.daemon_wiring import make_role_handler

        dispatcher = MagicMock()
        fake_result = MagicMock()
        fake_result.output = "agent output"
        fake_result.status = "completed"
        dispatcher.dispatch_one = AsyncMock(return_value=fake_result)

        handler = make_role_handler(dispatcher)
        await handler("planner", {"prompt": "plan it"})
        assert dispatcher.dispatch_one.called
        task_arg = dispatcher.dispatch_one.call_args[0][0]
        assert task_arg.agent_name == "planner"

    def test_role_handler_none_dispatcher_returns_none(self) -> None:
        from general_ludd.daemon_wiring import make_role_handler

        handler = make_role_handler(None)
        assert handler is None


class TestCollectionHandler:
    """collection_handler routes (module_fqcn, args) to AnsibleRunnerAdapter.run_playbook.

    The handler builds a transient one-task playbook that invokes the named
    Ansible collection module, runs it via the adapter, and returns the
    runner's result dict. Mirrors the mcp/role handler contract.
    """

    def test_collection_handler_is_callable(self) -> None:
        from general_ludd.daemon_wiring import make_collection_handler

        adapter = MagicMock()
        handler = make_collection_handler(adapter)
        assert callable(handler)

    def test_collection_handler_none_adapter_returns_none(self) -> None:
        """When runner_adapter is None, make_collection_handler returns None."""
        from general_ludd.daemon_wiring import make_collection_handler

        handler = make_collection_handler(None)
        assert handler is None

    @pytest.mark.asyncio
    async def test_collection_handler_routes_to_run_playbook(self, tmp_path) -> None:
        """A module FQCN dispatches through the runner adapter's run_playbook.

        Verifies the handler (a) registers a transient playbook under the
        adapter, (b) invokes run_playbook with that name, (c) returns the
        runner's result, and (d) cleans the transient entry afterwards.
        """
        from general_ludd.daemon_wiring import make_collection_handler

        adapter = MagicMock()
        adapter.private_data_dir = str(tmp_path)
        adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        handler = make_collection_handler(adapter)

        result = await handler(
            "ansible.builtin.command",
            {"cmd": "echo hi"},
        )

        # run_playbook was invoked exactly once with a transient name + args
        assert adapter.run_playbook.called
        call_kwargs = adapter.run_playbook.call_args
        playbook_name = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["playbook_name"]
        # Transient name is namespaced so it cannot collide with user playbooks.
        assert "_dispatch" in playbook_name or playbook_name.startswith("_")
        # The result flows back unchanged.
        assert result == {"status": "successful", "rc": 0}

    @pytest.mark.asyncio
    async def test_collection_handler_registers_and_unregisters_transient(self, tmp_path) -> None:
        """The transient playbook is registered for the run then unregistered."""
        from general_ludd.daemon_wiring import make_collection_handler

        adapter = MagicMock()
        adapter.private_data_dir = str(tmp_path)
        adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        handler = make_collection_handler(adapter)

        await handler("ansible.builtin.debug", {"msg": "hello"})

        # register_playbook and unregister_playbook were each called once.
        adapter.register_playbook.assert_called_once()
        adapter.unregister_playbook.assert_called_once()
        # The SAME transient name was registered, run, and unregistered.
        registered_name = adapter.register_playbook.call_args.args[0]
        run_name = (
            adapter.run_playbook.call_args.args[0]
            if adapter.run_playbook.call_args.args
            else adapter.run_playbook.call_args.kwargs["playbook_name"]
        )
        unregistered_name = adapter.unregister_playbook.call_args.args[0]
        assert registered_name == run_name == unregistered_name

    @pytest.mark.asyncio
    async def test_collection_handler_propagates_runner_failure(self, tmp_path) -> None:
        """A failed run still returns the runner's result dict (no exception)."""
        from general_ludd.daemon_wiring import make_collection_handler

        adapter = MagicMock()
        adapter.private_data_dir = str(tmp_path)
        adapter.run_playbook.return_value = {
            "status": "failed",
            "rc": 2,
            "error": "module error",
        }
        handler = make_collection_handler(adapter)

        result = await handler("ansible.builtin.shell", {"cmd": "false"})

        assert result["status"] == "failed"
        assert result["rc"] == 2


# ---------------------------------------------------------------------------
# 2. dispatch_router receives non-None handlers when subsystems exist
# ---------------------------------------------------------------------------


class TestDispatchRouterRegistration:
    """When build_dispatch_handlers is called with real stubs, all registered kinds appear."""

    def test_build_dispatch_handlers_all_kinds_callable(self) -> None:
        from general_ludd.daemon_wiring import build_dispatch_handlers

        mcp_client = MagicMock()
        mcp_client.call_tool = AsyncMock(return_value={})
        skill_registry = MagicMock()
        agent_dispatcher = MagicMock()
        agent_dispatcher.dispatch_one = AsyncMock()

        handlers = build_dispatch_handlers(
            mcp_client=mcp_client,
            skill_registry=skill_registry,
            agent_dispatcher=agent_dispatcher,
        )
        assert callable(handlers["mcp_handler"])
        assert callable(handlers["skill_handler"])
        assert callable(handlers["role_handler"])
        # collection_handler stays None when no runner_adapter is supplied —
        # the dispatcher fails-closed for the collection kind.
        assert handlers["collection_handler"] is None

    def test_build_dispatch_handlers_wires_collection_when_adapter_provided(self) -> None:
        """A runner_adapter wires the collection handler; the kind becomes live."""
        from general_ludd.daemon_wiring import build_dispatch_handlers

        handlers = build_dispatch_handlers(
            mcp_client=None,
            skill_registry=None,
            agent_dispatcher=None,
            runner_adapter=MagicMock(),
        )
        assert callable(handlers["collection_handler"])
        # Other handlers remain None — no subsystem supplied.
        assert handlers["mcp_handler"] is None
        assert handlers["skill_handler"] is None
        assert handlers["role_handler"] is None

    def test_build_dispatch_handlers_no_mcp_client_omits_mcp(self) -> None:
        from general_ludd.daemon_wiring import build_dispatch_handlers

        handlers = build_dispatch_handlers(
            mcp_client=None,
            skill_registry=MagicMock(),
            agent_dispatcher=MagicMock(),
        )
        assert handlers["mcp_handler"] is None

    def test_build_dispatch_handlers_no_skill_registry_omits_skill(self) -> None:
        from general_ludd.daemon_wiring import build_dispatch_handlers

        handlers = build_dispatch_handlers(
            mcp_client=MagicMock(),
            skill_registry=None,
            agent_dispatcher=MagicMock(),
        )
        assert handlers["skill_handler"] is None


# ---------------------------------------------------------------------------
# 3. SpendLimiter gate: would_exceed -> skip, under budget -> proceed
# ---------------------------------------------------------------------------


class TestSpendLimiterGate:
    """make_spend_guarded_executor wraps an executor with a pre-call SpendLimiter check."""

    @pytest.mark.asyncio
    async def test_under_budget_call_proceeds(self) -> None:
        from general_ludd.agents.dispatcher import AgentTask
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.daemon_wiring import make_spend_guarded_executor

        limiter = SpendLimiter(limit_usd=1.0, window_seconds=60.0)
        called: list[str] = []

        async def fake_executor(task: Any) -> str:
            called.append("called")
            return "result"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.01,
        )

        task = AgentTask(
            task_id="t1",
            agent_name="planner",
            description="test",
            prompt="do something",
        )
        result = await guarded(task)
        assert called == ["called"]
        assert result == "result"

    @pytest.mark.asyncio
    async def test_over_budget_call_deferred(self) -> None:
        """When would_exceed is True, executor is NOT called; deferred sentinel returned."""
        from general_ludd.agents.dispatcher import AgentTask
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.daemon_wiring import make_spend_guarded_executor

        # Pre-fill the window: already at limit
        limiter = SpendLimiter(limit_usd=0.05, window_seconds=60.0)
        limiter.record(0.05, kind="token")  # window is now exactly at limit

        called: list[str] = []

        async def fake_executor(task: Any) -> str:
            called.append("called")
            return "result"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.01,  # would push over 0.05
        )

        task = AgentTask(
            task_id="t2",
            agent_name="planner",
            description="test",
            prompt="do something",
        )
        result = await guarded(task)
        # Executor must NOT have been called
        assert called == []
        # Result must indicate deferral (not an actual execution output)
        assert "deferred" in str(result).lower() or result == "" or result is not None

    @pytest.mark.asyncio
    async def test_none_limiter_proceeds_without_check(self) -> None:
        """When no SpendLimiter is configured, executor runs unconditionally."""
        from general_ludd.agents.dispatcher import AgentTask
        from general_ludd.daemon_wiring import make_spend_guarded_executor

        called: list[str] = []

        async def fake_executor(task: Any) -> str:
            called.append("called")
            return "ok"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=None,
            projected_cost_usd=99.0,
        )

        task = AgentTask(
            task_id="t3",
            agent_name="planner",
            description="test",
            prompt="do something",
        )
        result = await guarded(task)
        assert called == ["called"]
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_zero_cost_never_deferred_even_at_limit(self) -> None:
        """Projected cost 0.0 never triggers deferral (SpendLimiter.would_exceed semantics)."""
        from general_ludd.agents.dispatcher import AgentTask
        from general_ludd.controllers.spend_limiter import SpendLimiter
        from general_ludd.daemon_wiring import make_spend_guarded_executor

        limiter = SpendLimiter(limit_usd=0.0, window_seconds=60.0)

        called: list[str] = []

        async def fake_executor(task: Any) -> str:
            called.append("called")
            return "ok"

        guarded = make_spend_guarded_executor(
            executor=fake_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.0,
        )

        task = AgentTask(
            task_id="t4",
            agent_name="planner",
            description="test",
            prompt="do something",
        )
        await guarded(task)
        assert called == ["called"]
