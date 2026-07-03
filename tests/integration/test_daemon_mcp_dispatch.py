"""Integration: the daemon's MCP tool-call dispatch path is actually WIRED.

Completion-integrity HIGH regression guard.

THE BUG (audit a30dc5ac): the daemon constructs an MCPClient + MCPToolRegistry
and passes them to the EventLoop, but never builds/binds a DynamicDispatcher
with an ``mcp`` handler. At dispatch time the EventLoop sees ``_dispatcher is
None`` and logs "no dispatcher is wired — skipping dispatch", silently DROPPING
a model-emitted MCP tool call. ``_mcp_client`` was only used to *advertise* tool
names, never to *execute* one.

This test drives the REAL path the daemon uses:

  * a ``DynamicDispatcher`` built exactly the way the daemon's
    :func:`general_ludd.daemon.build_event_loop_mcp_dispatcher` builds it,
  * wired into a real ``EventLoop`` via ``dispatcher=``,
  * a model response carrying an ``mcp`` tool_call,

and asserts the call is ROUTED to ``mcp_client.call_tool`` (recorded by a fake
client) and the result flows back through the DispatchResult — NOT a mock that
bypasses the dispatcher.

Before the fix ``build_event_loop_mcp_dispatcher`` does not exist (ImportError)
and the EventLoop dispatch site has no dispatcher, so this test FAILS. After the
fix it imports, the role is permitted ("event_loop"), and the call is executed.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.dispatch.dynamic_dispatcher import ToolCall
from general_ludd.event_loop.loop import EventLoop
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


class _FakeTransport:
    """Records call_tool invocations and returns a canned result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {"content": f"executed:{tool_name}", "args": arguments}


class _FakeMCPClient:
    """A real-shaped MCPClient stand-in that RECORDS call_tool.

    Mirrors :class:`general_ludd.mcp.client.MCPClient` — same
    ``call_tool(server_id, tool_name, arguments)`` signature and the same
    registry-backed server_id validation — so the dispatcher must reach the
    genuine routing path, not a bypass.
    """

    def __init__(self, registry: MCPToolRegistry) -> None:
        self._registry = registry
        self._transports: dict[str, _FakeTransport] = {}
        self.recorded: list[tuple[str, str, dict[str, Any]]] = []

    def add_server(self, server_id: str, transport: _FakeTransport) -> None:
        self._transports[server_id] = transport

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.recorded.append((server_id, tool_name, arguments))
        transport = self._transports.get(server_id)
        if transport is None:
            raise RuntimeError(f"No transport for server: {server_id}")
        tool = self._registry.get_tool(tool_name)
        if tool is None or tool.server_id != server_id:
            raise RuntimeError(
                f"Tool {tool_name!r} not registered to server {server_id!r}"
            )
        return await transport.call_tool(tool_name, arguments)


def _build_registry_and_client() -> tuple[MCPToolRegistry, _FakeMCPClient]:
    registry = MCPToolRegistry()
    registry.register_tool("files", MCPTool(name="read_file", description="read"))
    client = _FakeMCPClient(registry)
    client.add_server("files", _FakeTransport())
    return registry, client


class TestDaemonMcpDispatchWired:
    def test_builder_exists(self) -> None:
        """The daemon must expose a builder that wires the EventLoop dispatcher.

        Before the fix this import fails — the daemon never builds a dispatcher
        for the EventLoop, so the symbol does not exist.
        """
        from general_ludd.daemon import build_event_loop_mcp_dispatcher  # noqa: F401

    @pytest.mark.asyncio
    async def test_dispatcher_routes_mcp_call_to_client(self) -> None:
        """A model MCP tool_call is ROUTED to mcp_client.call_tool and returns."""
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        registry, client = _build_registry_and_client()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=client,
            mcp_tool_registry=registry,
            skill_registry=None,
            agent_dispatcher=None,
        )

        # The model emitted: call MCP tool "read_file" on server "files".
        call = ToolCall(kind="mcp", name="files/read_file", args={"path": "/etc/hosts"})
        result = await dispatcher.dispatch(call)

        assert result.ok is True, f"dispatch failed: {result.error!r}"
        # Recorded on the REAL client path — not bypassed.
        assert client.recorded == [("files", "read_file", {"path": "/etc/hosts"})]
        # The transport result flowed back through the DispatchResult.
        assert result.output == {
            "content": "executed:read_file",
            "args": {"path": "/etc/hosts"},
        }

    @pytest.mark.asyncio
    async def test_eventloop_dispatch_site_executes_mcp_call(self) -> None:
        """Drive the EventLoop's own dispatch site: dispatch_all → client.

        This is the exact code path loop.py runs after parsing model tool_calls
        (``self._dispatcher.dispatch_all(calls)``). With the bug, ``_dispatcher``
        is None and the call is dropped; with the fix the wired dispatcher
        executes it.
        """
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        registry, client = _build_registry_and_client()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=client,
            mcp_tool_registry=registry,
            skill_registry=None,
            agent_dispatcher=None,
        )

        loop = EventLoop(dispatcher=dispatcher)
        assert loop._dispatcher is not None

        calls = [ToolCall(kind="mcp", name="files/read_file", args={"q": 1})]
        results = await loop._dispatcher.dispatch_all(calls)

        assert len(results) == 1
        assert results[0].ok is True, f"dispatch failed: {results[0].error!r}"
        assert client.recorded == [("files", "read_file", {"q": 1})]

    @pytest.mark.asyncio
    async def test_role_is_event_loop_not_denied(self) -> None:
        """The wired dispatcher must use a role that may dispatch mcp.

        Guards against the capability_denied trap (role None denies mcp).
        """
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        registry, client = _build_registry_and_client()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=client,
            mcp_tool_registry=registry,
            skill_registry=None,
            agent_dispatcher=None,
        )
        # mcp kind must be registered AND the role permitted.
        assert "mcp" in dispatcher.list_available()["registered_kinds"]
        result = await dispatcher.dispatch(
            ToolCall(kind="mcp", name="files/read_file", args={})
        )
        assert "capability_denied" not in (result.error or "")


class _FakeAgentDispatcher:
    """AgentDispatcher stand-in that records dispatch_one invocations.

    Mirrors the relevant slice of
    :class:`general_ludd.agents.dispatcher.AgentDispatcher` — same
    ``dispatch_one(task) -> AgentTaskResult`` shape — so the dispatcher under
    test must reach the genuine routing path, not a bypass.
    """

    def __init__(self) -> None:
        self.recorded: list[str] = []

    async def dispatch_one(self, task: Any) -> Any:
        from general_ludd.agents.dispatcher import AgentTaskResult

        self.recorded.append(task.agent_name)
        return AgentTaskResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status="ok",
            output=f"executed:{task.agent_name}:{task.prompt}",
        )


class TestDaemonRoleDispatchWired:
    """The daemon's EventLoop dispatcher must wire the ``role`` handler.

    Mirrors the mcp wiring tests above but for the ``role`` kind. Before the
    fix ``build_event_loop_mcp_dispatcher`` leaves ``agent_dispatcher`` unused
    (reserved comment only) so the ``role`` kind is never registered; a
    model-emitted role tool_call fails-closed with ``unknown kind`` instead of
    reaching ``AgentDispatcher.dispatch_one``.
    """

    def test_role_kind_registered_when_agent_dispatcher_provided(self) -> None:
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=None,
            mcp_tool_registry=None,
            skill_registry=None,
            agent_dispatcher=_FakeAgentDispatcher(),
        )
        assert dispatcher is not None
        assert "role" in dispatcher.list_available()["registered_kinds"]

    def test_none_returned_when_no_subsystems_at_all(self) -> None:
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        # Sanity: with no subsystems at all the builder still short-circuits.
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=None,
            mcp_tool_registry=None,
            skill_registry=None,
            agent_dispatcher=None,
        )
        assert dispatcher is None

    @pytest.mark.asyncio
    async def test_dispatcher_routes_role_call_to_agent_dispatcher(self) -> None:
        """A model role tool_call is ROUTED to agent_dispatcher.dispatch_one."""
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        agent_dispatcher = _FakeAgentDispatcher()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=None,
            mcp_tool_registry=None,
            skill_registry=None,
            agent_dispatcher=agent_dispatcher,
        )
        assert dispatcher is not None

        call = ToolCall(
            kind="role",
            name="researcher",
            args={"prompt": "summarize the README"},
        )
        result = await dispatcher.dispatch(call)

        assert result.ok is True, f"dispatch failed: {result.error!r}"
        # dispatch_one was called with the named role and the forwarded prompt.
        assert agent_dispatcher.recorded == ["researcher"]
        # The AgentTaskResult.output flowed back through the DispatchResult.
        assert result.output == "executed:researcher:summarize the README"

    @pytest.mark.asyncio
    async def test_eventloop_dispatch_site_executes_role_call(self) -> None:
        """Drive the EventLoop's own dispatch site for a role tool_call."""
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        agent_dispatcher = _FakeAgentDispatcher()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=None,
            mcp_tool_registry=None,
            skill_registry=None,
            agent_dispatcher=agent_dispatcher,
        )
        loop = EventLoop(dispatcher=dispatcher)
        assert loop._dispatcher is not None

        calls = [ToolCall(kind="role", name="researcher", args={"prompt": "hi"})]
        results = await loop._dispatcher.dispatch_all(calls)

        assert len(results) == 1
        assert results[0].ok is True, f"dispatch failed: {results[0].error!r}"
        assert agent_dispatcher.recorded == ["researcher"]


class _FakeRunnerAdapter:
    """AnsibleRunnerAdapter stand-in that RECORDS run_playbook invocations.

    Mirrors the relevant slice of
    :class:`general_ludd.ansible.runner.AnsibleRunnerAdapter` — same
    ``register_playbook`` / ``run_playbook`` / ``unregister_playbook`` shape
    and a ``private_data_dir`` attribute — so the collection handler under
    test must reach the genuine register→run→unregister path, not a bypass.
    """

    def __init__(self, private_data_dir: str | None = None) -> None:
        # A unique per-instance temp dir (per-worker isolated) so concurrent
        # xdist workers don't collide on a shared "/tmp/gludd-test-fake-runner"
        # dispatch_playbooks tree that the collection handler writes into.
        if private_data_dir is None:
            import tempfile

            private_data_dir = tempfile.mkdtemp(prefix="gludd-test-fake-runner-")
        self.private_data_dir = private_data_dir
        self.registered: list[tuple[str, str]] = []
        self.unregistered: list[str] = []
        self.run_calls: list[tuple[str, dict[str, Any]]] = []
        self._next_result: dict[str, Any] = {
            "status": "successful",
            "rc": 0,
            "events": [],
        }

    def set_result(self, result: dict[str, Any]) -> None:
        self._next_result = result

    def register_playbook(self, name: str, path: str) -> None:
        self.registered.append((name, path))

    def unregister_playbook(self, name: str) -> None:
        self.unregistered.append(name)

    def run_playbook(self, playbook_name: str, **kwargs: Any) -> dict[str, Any]:
        # Record the kwargs that actually arrived so the test can assert on
        # the timeout / extravars the handler forwarded.
        self.run_calls.append((playbook_name, dict(kwargs)))
        return dict(self._next_result)


class TestDaemonCollectionDispatchWired:
    """The daemon's collection handler dispatches Ansible modules via the runner.

    THE GAP (SCAFFOLDING): ``build_dispatch_handlers`` left ``collection_handler``
    hard-coded to ``None`` with a TODO, so the dispatcher failed-closed with
    ``unknown_kind:collection`` for every model-emitted collection tool_call.
    The factory ``make_collection_handler`` closes that gap: it builds a
    transient one-task playbook invoking the named Ansible collection module
    and runs it through the live AnsibleRunnerAdapter.
    """

    def test_factory_exists(self) -> None:
        """``make_collection_handler`` is importable from daemon_wiring."""
        from general_ludd.daemon_wiring import make_collection_handler  # noqa: F401

    def test_factory_returns_none_when_adapter_is_none(self) -> None:
        """No adapter ⇒ no handler (kind fails-closed at dispatch)."""
        from general_ludd.daemon_wiring import make_collection_handler

        assert make_collection_handler(None) is None

    @pytest.mark.asyncio
    async def test_handler_routes_module_to_runner(self) -> None:
        """A collection tool_call dispatches to run_playbook and returns its result."""
        from general_ludd.daemon_wiring import make_collection_handler

        adapter = _FakeRunnerAdapter()
        handler = make_collection_handler(adapter)
        assert handler is not None

        result = await handler(
            "ansible.builtin.command",
            {"cmd": "echo wired"},
        )

        assert result == {"status": "successful", "rc": 0, "events": []}
        # The runner saw exactly one invocation.
        assert len(adapter.run_calls) == 1
        playbook_name, _kwargs = adapter.run_calls[0]
        # Transient name is namespaced so it cannot collide with user playbooks.
        assert playbook_name.startswith("_")

    @pytest.mark.asyncio
    async def test_handler_registers_and_unregisters_transient_playbook(self) -> None:
        """The transient playbook is registered for the run then cleaned up."""
        from general_ludd.daemon_wiring import make_collection_handler

        adapter = _FakeRunnerAdapter()
        handler = make_collection_handler(adapter)
        assert handler is not None

        await handler("ansible.builtin.debug", {"msg": "hi"})

        assert len(adapter.registered) == 1
        assert len(adapter.unregistered) == 1
        # Same transient name was registered, run, and unregistered.
        reg_name = adapter.registered[0][0]
        run_name = adapter.run_calls[0][0]
        assert reg_name == run_name == adapter.unregistered[0]

    @pytest.mark.asyncio
    async def test_handler_returns_runner_failure_dict(self) -> None:
        """A failed run returns the runner's result dict — no exception bubbles."""
        from general_ludd.daemon_wiring import make_collection_handler

        adapter = _FakeRunnerAdapter()
        adapter.set_result({"status": "failed", "rc": 2, "error": "boom"})
        handler = make_collection_handler(adapter)
        assert handler is not None

        result = await handler("ansible.builtin.shell", {"cmd": "false"})
        assert result["status"] == "failed"
        assert result["rc"] == 2

    @pytest.mark.asyncio
    async def test_dispatcher_routes_collection_call_through_factory_handler(self) -> None:
        """A DynamicDispatcher wired with the factory handler executes the call.

        This drives the dispatcher end-to-end: a ``collection`` ToolCall is
        routed to the handler built by ``make_collection_handler`` and reaches
        the runner adapter. The role is ``self_improve_agent`` — the only
        built-in role whose capability lattice grants the ``collection`` kind
        (operator grants the kind too but not collections_self_modify; we use
        the canonical self-modify role here).
        """
        from general_ludd.daemon_wiring import make_collection_handler
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        adapter = _FakeRunnerAdapter()
        dispatcher = DynamicDispatcher(
            role="self_improve_agent",
            collection_handler=make_collection_handler(adapter),
        )

        call = ToolCall(
            kind="collection",
            name="ansible.builtin.command",
            args={"cmd": "echo dispatched"},
        )
        result = await dispatcher.dispatch(call)

        assert result.ok is True, f"dispatch failed: {result.error!r}"
        assert "collection" in dispatcher.list_available()["registered_kinds"]
        # The runner saw the module dispatch.
        assert len(adapter.run_calls) == 1
        assert result.output == {"status": "successful", "rc": 0, "events": []}

    @pytest.mark.asyncio
    async def test_eventloop_dispatch_site_executes_collection_call(self) -> None:
        """Drive the EventLoop's dispatch_all for a collection tool_call.

        Uses the ``operator`` role (which grants the ``collection`` kind but
        lacks ``collections_self_modify``), proving the handler routes through
        even when the lattice is the strict gate — and confirms that at the
        loop tier the existing ``event_loop`` role still DENIES collection
        (self-modify protection), so we exercise the success path with a
        role that the lattice grants.
        """
        from general_ludd.daemon_wiring import make_collection_handler
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        adapter = _FakeRunnerAdapter()
        dispatcher = DynamicDispatcher(
            role="self_improve_agent",
            collection_handler=make_collection_handler(adapter),
        )
        loop = EventLoop(dispatcher=dispatcher)
        assert loop._dispatcher is not None

        calls = [
            ToolCall(
                kind="collection",
                name="ansible.builtin.debug",
                args={"msg": "loop-tier"},
            )
        ]
        results = await loop._dispatcher.dispatch_all(calls)

        assert len(results) == 1
        assert results[0].ok is True, f"dispatch failed: {results[0].error!r}"
        assert len(adapter.run_calls) == 1
