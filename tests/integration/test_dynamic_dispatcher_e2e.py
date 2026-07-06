"""E2E integration proof for DynamicDispatcher (#26).

Exercises the full dispatch pipeline: structured_tool_calls_to_calls →
DynamicDispatcher.dispatch → role capability gating → handler invocation.

Tests:
  - structured_tool_calls_to_calls converts OpenAI-nested shape to ToolCall list
  - DynamicDispatcher.dispatch routes MCP tool calls to injected handler
  - DynamicDispatcher.dispatch denies unregistered kinds (fail-closed)
  - DynamicDispatcher.dispatch enforces capability lattice per role
  - UNRESTRICTED_ROLE bypasses the capability gate
  - dispatch_all returns ordered results for multiple calls
  - parse_tool_calls handles dict, JSON string, and malformed inputs
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from general_ludd.dispatch.dynamic_dispatcher import (
    PRIVILEGED_KINDS,
    UNRESTRICTED_ROLE,
    DispatchResult,
    DynamicDispatcher,
    ToolCall,
    parse_tool_calls,
    structured_tool_calls_to_calls,
)

# ---------------------------------------------------------------------------
# structured_tool_calls_to_calls
# ---------------------------------------------------------------------------


class TestStructuredToolCallsToCalls:
    def test_converts_openai_shape_to_tool_calls(self):
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "/tmp/test.txt"}',
                },
            },
        ]
        result = structured_tool_calls_to_calls(tool_calls)
        assert len(result) == 1
        assert result[0].kind == "mcp"
        assert result[0].name == "read_file"
        assert result[0].args == {"path": "/tmp/test.txt"}

    def test_arguments_already_dict(self):
        tool_calls = [
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": {"path": "/tmp/out.txt", "content": "hi"},
                },
            },
        ]
        result = structured_tool_calls_to_calls(tool_calls)
        assert len(result) == 1
        assert result[0].args == {"path": "/tmp/out.txt", "content": "hi"}

    def test_returns_empty_for_none(self):
        assert structured_tool_calls_to_calls(None) == []
        assert structured_tool_calls_to_calls([]) == []

    def test_skips_malformed_entries(self):
        tool_calls = [
            {"id": "call_3", "type": "function", "function": None},
            {
                "id": "call_4",
                "type": "function",
                "function": {"name": "valid", "arguments": "{}"},
            },
        ]
        result = structured_tool_calls_to_calls(tool_calls)
        assert len(result) == 1
        assert result[0].name == "valid"

    def test_skips_entries_without_name(self):
        tool_calls = [
            {
                "id": "call_5",
                "type": "function",
                "function": {"name": "", "arguments": "{}"},
            },
            {
                "id": "call_6",
                "type": "function",
                "function": {"arguments": "{}"},
            },
        ]
        result = structured_tool_calls_to_calls(tool_calls)
        assert len(result) == 0

    def test_invalid_json_arguments_yields_empty_dict(self):
        tool_calls = [
            {
                "id": "call_7",
                "type": "function",
                "function": {
                    "name": "broken_json",
                    "arguments": "{not valid}",
                },
            },
        ]
        result = structured_tool_calls_to_calls(tool_calls)
        assert len(result) == 1
        assert result[0].args == {}

    def test_truncates_long_names(self):
        tool_calls = [
            {
                "id": "call_8",
                "type": "function",
                "function": {"name": "X" * 300, "arguments": "{}"},
            },
        ]
        result = structured_tool_calls_to_calls(tool_calls)
        assert len(result[0].name) == 256

    def test_non_dict_tool_call_skipped(self):
        tool_calls = ["not_a_dict"]
        assert structured_tool_calls_to_calls(cast(Any, tool_calls)) == []


# ---------------------------------------------------------------------------
# parse_tool_calls
# ---------------------------------------------------------------------------


class TestParseToolCalls:
    def test_parses_tool_calls_wrapper_dict(self):
        model_output = {
            "tool_calls": [
                {"kind": "mcp", "name": "search", "args": {"q": "test"}},
            ],
        }
        result = parse_tool_calls(model_output)
        assert len(result) == 1
        assert result[0].kind == "mcp"
        assert result[0].name == "search"
        assert result[0].args == {"q": "test"}

    def test_parses_single_tool_call_dict(self):
        model_output = {"kind": "skill", "name": "lint"}
        result = parse_tool_calls(model_output)
        assert len(result) == 1
        assert result[0].kind == "skill"
        assert result[0].name == "lint"

    def test_parses_json_string(self):
        model_output = '{"kind": "role", "name": "implement_change"}'
        result = parse_tool_calls(model_output)
        assert len(result) == 1
        assert result[0].kind == "role"

    def test_invalid_json_returns_empty(self):
        assert parse_tool_calls("not json") == []

    def test_empty_dict_returns_empty(self):
        assert parse_tool_calls({}) == []

    def test_non_dict_non_string_returns_empty(self):
        assert parse_tool_calls(42) == []

    def test_missing_kind_becomes_unknown(self):
        model_output = {"name": "no_kind"}
        result = parse_tool_calls(model_output)
        assert len(result) == 1
        assert result[0].kind == "unknown"

    def test_missing_name_skipped(self):
        model_output = {"kind": "mcp"}
        result = parse_tool_calls(model_output)
        assert len(result) == 0

    def test_skips_non_dict_in_call_list(self):
        model_output = {
            "tool_calls": [
                42,
                {"kind": "mcp", "name": "valid"},
            ],
        }
        result = parse_tool_calls(model_output)
        assert len(result) == 1
        assert result[0].name == "valid"

    def test_truncates_kind_and_name(self):
        model_output = {"kind": "X" * 100, "name": "Y" * 300}
        result = parse_tool_calls(model_output)
        assert len(result[0].kind) == 64
        assert len(result[0].name) == 256

    def test_non_string_name_skipped(self):
        model_output = {"kind": "mcp", "name": 123}
        result = parse_tool_calls(model_output)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# DynamicDispatcher.dispatch — routing and handler invocation
# ---------------------------------------------------------------------------


class TestDynamicDispatcherDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_to_registered_handler(self):
        handler = MagicMock(return_value={"result": "ok"})
        dispatcher = DynamicDispatcher(
            mcp_handler=handler, role=UNRESTRICTED_ROLE,
        )

        result = await dispatcher.dispatch(ToolCall(kind="mcp", name="read"))

        assert result.ok is True
        assert result.kind == "mcp"
        assert result.name == "read"
        assert result.output == {"result": "ok"}
        handler.assert_called_once_with("read", {})

    @pytest.mark.asyncio
    async def test_handles_coroutine_handler(self):
        async def async_handler(name: str, args: dict):
            return {"async": True}

        dispatcher = DynamicDispatcher(
            mcp_handler=async_handler, role=UNRESTRICTED_ROLE,
        )
        result = await dispatcher.dispatch(ToolCall(kind="mcp", name="run"))
        assert result.ok is True
        assert result.output == {"async": True}

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        def failing_handler(name: str, args: dict):
            raise ValueError("bad")

        dispatcher = DynamicDispatcher(
            mcp_handler=failing_handler, role=UNRESTRICTED_ROLE,
        )
        result = await dispatcher.dispatch(ToolCall(kind="mcp", name="fail"))
        assert result.ok is False
        assert result.error == "handler_error"

    @pytest.mark.asyncio
    async def test_unknown_kind_fail_closed(self):
        dispatcher = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = await dispatcher.dispatch(ToolCall(kind="bogus", name="x"))
        assert result.ok is False
        assert "unknown_kind" in (result.error or "")

    @pytest.mark.asyncio
    async def test_capability_denied_for_none_role(self):
        dispatcher = DynamicDispatcher(role=None)
        for kind in PRIVILEGED_KINDS:
            result = await dispatcher.dispatch(
                ToolCall(kind=kind, name="test"),
            )
            assert result.ok is False, f"kind {kind} should be denied"
            assert result.error == "capability_denied"

    @pytest.mark.asyncio
    async def test_unrestricted_role_bypasses_capability(self):
        handler = MagicMock(return_value="ok")
        dispatcher = DynamicDispatcher(
            role_handler=handler, role=UNRESTRICTED_ROLE,
        )
        result = await dispatcher.dispatch(ToolCall(kind="role", name="test"))
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_pass_args_to_handler(self):
        handler = MagicMock(return_value=None)
        dispatcher = DynamicDispatcher(
            skill_handler=handler, role=UNRESTRICTED_ROLE,
        )
        await dispatcher.dispatch(
            ToolCall(kind="skill", name="render", args={"key": "val"}),
        )
        handler.assert_called_once_with("render", {"key": "val"})

    @pytest.mark.asyncio
    async def test_dispatch_all_results_in_order(self):
        handler = MagicMock(side_effect=["a", "b", "c"])
        dispatcher = DynamicDispatcher(
            mcp_handler=handler, role=UNRESTRICTED_ROLE,
        )
        calls = [
            ToolCall(kind="mcp", name="1"),
            ToolCall(kind="mcp", name="2"),
            ToolCall(kind="mcp", name="3"),
        ]
        results = await dispatcher.dispatch_all(calls)
        assert len(results) == 3
        assert [r.output for r in results] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_list_available(self):
        dispatcher = DynamicDispatcher(
            mcp_handler=MagicMock(),
            skill_handler=MagicMock(),
            role=UNRESTRICTED_ROLE,
        )
        kinds = dispatcher.list_available()["registered_kinds"]
        assert set(kinds) == {"mcp", "skill"}

    @pytest.mark.asyncio
    async def test_dispatch_result_to_dict(self):
        r = DispatchResult(
            ok=True, kind="mcp", name="test", output="yes",
        )
        d = r.to_dict()
        assert d == {
            "ok": True, "kind": "mcp", "name": "test",
            "output": "yes", "error": None,
        }

    @pytest.mark.asyncio
    async def test_error_result_to_dict(self):
        r = DispatchResult(
            ok=False, kind="role", name="x", error="capability_denied",
        )
        d = r.to_dict()
        assert d["ok"] is False
        assert d["error"] == "capability_denied"
        assert d["output"] is None

    @pytest.mark.asyncio
    async def test_collection_handler_registered_and_dispatched(self):
        handler = MagicMock(return_value={"col": 1})
        dispatcher = DynamicDispatcher(
            collection_handler=handler, role=UNRESTRICTED_ROLE,
        )
        result = await dispatcher.dispatch(
            ToolCall(kind="collection", name="list"),
        )
        assert result.ok is True
        assert result.output == {"col": 1}


# ---------------------------------------------------------------------------
# Capability lattice integration
# ---------------------------------------------------------------------------


class TestCapabilityLatticeIntegration:
    def test_unrestricted_role_is_object_identity(self):
        assert UNRESTRICTED_ROLE is not None
        assert type(UNRESTRICTED_ROLE) is object

    def test_privileged_kinds_covers_all_handlers(self):
        assert "role" in PRIVILEGED_KINDS
        assert "collection" in PRIVILEGED_KINDS
        assert "mcp" in PRIVILEGED_KINDS
        assert "skill" in PRIVILEGED_KINDS

    @pytest.mark.asyncio
    async def test_role_constructor_stores_role(self):
        dispatcher = DynamicDispatcher(
            mcp_handler=MagicMock(), role="worker",
        )
        assert dispatcher._role == "worker"

    @pytest.mark.asyncio
    async def test_no_handlers_constructor(self):
        dispatcher = DynamicDispatcher()
        kinds = dispatcher.list_available()["registered_kinds"]
        assert kinds == []
