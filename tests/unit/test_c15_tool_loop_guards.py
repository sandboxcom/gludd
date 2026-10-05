"""C15 — Tool-call loop security guards (P1).

Four defects fixed in ``execution/tool_loop.py`` (+ ``dispatch/variable_store.py``):

1. Capability lattice bypassed on the Phase-2 (per-tool-call) loop — the entry
   ``check_dispatch(role, "mcp")`` fires once, but each individual tool call
   inside the round loop reached ``call_tool`` with no further lattice check.
2. No per-response tool-call cap — ``for tc in tool_calls:`` had no bound, so a
   single model response bundling N tool calls fanned out N unbounded calls.
3. Tool-call args never validated against the tool's ``input_schema`` before
   ``call_tool`` — a malformed / mistyped payload reached the tool.
4. VariableStore key injection — a model-controlled dispatch ``name`` could
   collide with the ``dispatch__last__*`` sentinel keys, and arbitrary keys with
   path separators / traversal could be written into the store.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
from general_ludd.dispatch.variable_store import VariableStore, apply_results
from general_ludd.execution.tool_loop import ToolCallLoop
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


class _Resp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls
        self.usage_metadata = {"input_tokens": 0, "output_tokens": 0}


def _tc(name, args, call_id="call-1"):
    return {"id": call_id, "function": {"name": name, "arguments": args}}


def _wiring(input_schema=None):
    registry = MCPToolRegistry()
    registry.register_tool(
        "fs",
        MCPTool(name="read_file", server_id="fs", input_schema=input_schema or {}),
    )
    mcp_client = MagicMock()
    mcp_client.list_tools = AsyncMock(
        return_value=[MCPTool(name="read_file", server_id="fs", input_schema=input_schema or {})]
    )
    mcp_client.call_tool = AsyncMock(return_value={"ok": True})
    job = MagicMock()
    job.job_id = "JOB-C15"
    job.work_type = "analysis"
    return registry, mcp_client, job


# --------------------------------------------------------------------------- #
# Defect 1 — Phase-2 loop routes each tool call through the capability lattice
# --------------------------------------------------------------------------- #
class TestPhase2CapabilityCheck:
    @pytest.mark.asyncio
    async def test_phase2_routes_through_capability_check(self, monkeypatch):
        """A per-tool-call lattice denial must block ``call_tool`` even though the
        entry gate passed. The entry ``check_dispatch`` uses the real lattice
        (role "event_loop" holds "mcp"); patching the per-call ``role_may_dispatch``
        seam to deny proves the Phase-2 loop consults the lattice per call and
        fails closed instead of reaching MCP.
        """
        registry, mcp_client, job = _wiring()

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="done"),
            ]
        )

        seen: list[tuple[str, str]] = []

        def _deny(role, kind):
            seen.append((role, kind))
            return False

        monkeypatch.setattr("general_ludd.execution.tool_loop.role_may_dispatch", _deny)

        loop = ToolCallLoop(
            gateway,
            mcp_client=mcp_client,
            mcp_registry=registry,
            role="event_loop",
        )

        result = await asyncio.wait_for(loop.run_with_tools(job, "sys", "user"), timeout=5)

        # The per-call lattice was consulted for the mcp call.
        assert ("event_loop", "mcp") in seen
        # Fail-closed: the denied call never reached MCP.
        mcp_client.call_tool.assert_not_called()
        # The model's next turn saw a capability-denied tool message (never an
        # orphaned tool_call_id).
        second_kwargs = gateway.call_model.call_args_list[1].kwargs
        tool_msgs = [m for m in second_kwargs["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call-1"
        assert "capability" in tool_msgs[0]["content"].lower()
        assert result == "done"

    @pytest.mark.asyncio
    async def test_phase2_allowed_role_still_executes(self):
        """Regression: a role that holds "mcp" at both entry and per-call runs the
        tool (the guard must not break the happy path)."""
        registry, mcp_client, job = _wiring()
        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "x"})]),
                _Resp(content="done"),
            ]
        )
        loop = ToolCallLoop(
            gateway,
            mcp_client=mcp_client,
            mcp_registry=registry,
            role="event_loop",
        )
        result = await asyncio.wait_for(loop.run_with_tools(job, "sys", "user"), timeout=5)
        assert result == "done"
        mcp_client.call_tool.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Defect 2 — per-response tool-call cap
# --------------------------------------------------------------------------- #
class TestPerResponseCap:
    @pytest.mark.asyncio
    async def test_per_response_tool_call_cap_enforced(self):
        """A response bundling 25 tool calls must be truncated to the cap (20);
        the 5 rejected calls each get an answering tool message (never orphaned)."""
        from general_ludd.execution.tool_loop import MAX_TOOL_CALLS_PER_RESPONSE

        registry, mcp_client, job = _wiring()
        over = MAX_TOOL_CALLS_PER_RESPONSE + 5
        calls = [_tc("read_file", {"path": f"f{i}"}, call_id=f"call-{i}") for i in range(over)]

        gateway = MagicMock()
        gateway.call_model = MagicMock(side_effect=[_Resp(tool_calls=calls), _Resp(content="done")])
        loop = ToolCallLoop(
            gateway,
            mcp_client=mcp_client,
            mcp_registry=registry,
        )
        result = await asyncio.wait_for(loop.run_with_tools(job, "sys", "user"), timeout=5)
        assert result == "done"
        # Exactly the cap number of calls actually executed.
        assert mcp_client.call_tool.await_count == MAX_TOOL_CALLS_PER_RESPONSE

        second_kwargs = gateway.call_model.call_args_list[1].kwargs
        tool_msgs = [m for m in second_kwargs["messages"] if m.get("role") == "tool"]
        # Every one of the 25 requested ids is answered (20 results + 5 rejections).
        assert len(tool_msgs) == over
        rejected = [m for m in tool_msgs if "cap" in m["content"].lower()]
        assert len(rejected) == 5
        answered_ids = {m["tool_call_id"] for m in tool_msgs}
        assert answered_ids == {f"call-{i}" for i in range(over)}

    def test_cap_matches_dispatch_router_constant(self):
        """Drift guard: the loop cap must equal the HTTP dispatch router cap."""
        from general_ludd.execution.tool_loop import MAX_TOOL_CALLS_PER_RESPONSE
        from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST

        assert MAX_TOOL_CALLS_PER_RESPONSE == MAX_CALLS_PER_REQUEST


# --------------------------------------------------------------------------- #
# Defect 3 — args validated against input_schema
# --------------------------------------------------------------------------- #
_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


class TestArgSchemaValidation:
    @pytest.mark.asyncio
    async def test_tool_args_validated_against_schema(self):
        """Args missing a required property must be rejected WITHOUT calling the
        tool; the model's next turn sees a validation-error tool message."""
        registry, mcp_client, job = _wiring(input_schema=_SCHEMA)
        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"wrong": "field"})]),
                _Resp(content="done"),
            ]
        )
        loop = ToolCallLoop(
            gateway,
            mcp_client=mcp_client,
            mcp_registry=registry,
        )
        result = await asyncio.wait_for(loop.run_with_tools(job, "sys", "user"), timeout=30)
        assert result == "done"
        mcp_client.call_tool.assert_not_called()
        second_kwargs = gateway.call_model.call_args_list[1].kwargs
        tool_msgs = [m for m in second_kwargs["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call-1"
        assert "valid" in tool_msgs[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_wrong_type_rejected(self):
        """A property of the wrong type is rejected without reaching the tool."""
        registry, mcp_client, job = _wiring(input_schema=_SCHEMA)
        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": 123})]),
                _Resp(content="done"),
            ]
        )
        loop = ToolCallLoop(
            gateway,
            mcp_client=mcp_client,
            mcp_registry=registry,
        )
        result = await asyncio.wait_for(loop.run_with_tools(job, "sys", "user"), timeout=30)
        assert result == "done"
        mcp_client.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_args_pass_schema_and_execute(self):
        """Schema-conformant args run the tool (guard must not break valid calls)."""
        registry, mcp_client, job = _wiring(input_schema=_SCHEMA)
        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"path": "ok.txt"})]),
                _Resp(content="done"),
            ]
        )
        loop = ToolCallLoop(
            gateway,
            mcp_client=mcp_client,
            mcp_registry=registry,
        )
        result = await asyncio.wait_for(loop.run_with_tools(job, "sys", "user"), timeout=5)
        assert result == "done"
        mcp_client.call_tool.assert_awaited_once_with("fs", "read_file", {"path": "ok.txt"})

    @pytest.mark.asyncio
    async def test_empty_schema_is_backward_compatible(self):
        """A tool with an empty input_schema is not gated (back-compat)."""
        registry, mcp_client, job = _wiring(input_schema={})
        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("read_file", {"anything": "goes"})]),
                _Resp(content="done"),
            ]
        )
        loop = ToolCallLoop(
            gateway,
            mcp_client=mcp_client,
            mcp_registry=registry,
        )
        result = await asyncio.wait_for(loop.run_with_tools(job, "sys", "user"), timeout=5)
        assert result == "done"
        mcp_client.call_tool.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Defect 4 — VariableStore key injection
# --------------------------------------------------------------------------- #
class TestVariableStoreKeyInjection:
    def test_variable_store_key_injection_blocked(self):
        """A key carrying a path separator / traversal must be rejected by set()."""
        store = VariableStore()
        for bad in ("../../etc/passwd", "a/b", "a\\b", "x\x00y"):
            with pytest.raises(ValueError):
                store.set("dispatch", bad, "pwned")
        # A benign key with the double-underscore flattening convention still works.
        store.set("dispatch", "tool_a__ok", True)
        assert store.get("dispatch", "tool_a__ok") is True

    def test_reserved_last_name_cannot_clobber_sentinel(self):
        """A model-controlled dispatch name of ``last`` must not overwrite the
        unconditional ``dispatch__last__*`` sentinel written for the real last
        result."""
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="last", output="INJECTED", error=None),
            DispatchResult(ok=True, kind="mcp", name="real", output="genuine", error=None),
        ]
        apply_results(store, results)
        # The sentinel reflects the ACTUAL last result, not the injected one.
        assert store.get("dispatch", "last__name") == "real"
        assert store.get("dispatch", "last__output") == "genuine"
        # The malicious "last"-named result is still stored, but under an escaped
        # key that cannot collide with the sentinel.
        assert store.get("dispatch", "last__output") != "INJECTED"
