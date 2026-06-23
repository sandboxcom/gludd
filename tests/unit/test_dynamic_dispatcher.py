"""TDD tests for DynamicDispatcher and parse_tool_calls.

All handlers are FAKE (lambda or dict-based) — no live daemon required.
"""

from __future__ import annotations

import json

import pytest

from general_ludd.dispatch.dynamic_dispatcher import (
    UNRESTRICTED_ROLE,
    DispatchResult,
    DynamicDispatcher,
    ToolCall,
    parse_tool_calls,
)

# ---------------------------------------------------------------------------
# parse_tool_calls — valid inputs
# ---------------------------------------------------------------------------

class TestParseToolCallsValid:
    def test_single_dict_with_kind_and_name(self):
        raw = {"kind": "role", "name": "validator", "args": {"target": "src/"}}
        calls = parse_tool_calls(raw)
        assert len(calls) == 1
        assert calls[0].kind == "role"
        assert calls[0].name == "validator"
        assert calls[0].args == {"target": "src/"}

    def test_single_dict_no_args_defaults_empty(self):
        raw = {"kind": "skill", "name": "grep-skill"}
        calls = parse_tool_calls(raw)
        assert len(calls) == 1
        assert calls[0].args == {}

    def test_tool_calls_list_shape(self):
        raw = {
            "tool_calls": [
                {"kind": "mcp", "name": "filesystem", "args": {"op": "read"}},
                {"kind": "skill", "name": "summarise", "args": {}},
            ]
        }
        calls = parse_tool_calls(raw)
        assert len(calls) == 2
        assert calls[0].kind == "mcp"
        assert calls[1].kind == "skill"

    def test_json_string_single_call(self):
        payload = json.dumps({"kind": "collection", "name": "tasks", "args": {}})
        calls = parse_tool_calls(payload)
        assert len(calls) == 1
        assert calls[0].kind == "collection"

    def test_json_string_tool_calls_list(self):
        payload = json.dumps({
            "tool_calls": [
                {"kind": "role", "name": "planner"},
            ]
        })
        calls = parse_tool_calls(payload)
        assert len(calls) == 1

    def test_args_defaults_to_empty_when_not_dict(self):
        """Non-dict args are silently replaced with {} rather than crashing."""
        raw = {"kind": "mcp", "name": "tool", "args": "not-a-dict"}
        calls = parse_tool_calls(raw)
        assert calls[0].args == {}

    def test_tool_calls_skips_items_without_name(self):
        raw = {
            "tool_calls": [
                {"kind": "role"},          # no name → skip
                {"kind": "skill", "name": "ok"},
            ]
        }
        calls = parse_tool_calls(raw)
        assert len(calls) == 1
        assert calls[0].name == "ok"


# ---------------------------------------------------------------------------
# parse_tool_calls — malformed inputs
# ---------------------------------------------------------------------------

class TestParseToolCallsMalformed:
    def test_empty_string_returns_empty(self):
        assert parse_tool_calls("") == []

    def test_non_json_string_returns_empty(self):
        assert parse_tool_calls("not json at all") == []

    def test_plain_list_returns_empty(self):
        # Top-level list is not the expected shape
        assert parse_tool_calls([]) == []  # type: ignore[arg-type]

    def test_empty_dict_returns_empty(self):
        assert parse_tool_calls({}) == []

    def test_dict_without_kind_and_name_returns_empty(self):
        assert parse_tool_calls({"foo": "bar"}) == []

    def test_tool_calls_key_not_list_returns_empty(self):
        raw = {"tool_calls": "not-a-list"}
        assert parse_tool_calls(raw) == []

    def test_json_null_returns_empty(self):
        assert parse_tool_calls("null") == []

    def test_json_number_returns_empty(self):
        assert parse_tool_calls("42") == []


# ---------------------------------------------------------------------------
# DynamicDispatcher — routing per kind
# ---------------------------------------------------------------------------

class TestDynamicDispatcherRouting:
    def _make_dispatcher(self) -> DynamicDispatcher:
        return DynamicDispatcher(
            role_handler=lambda name, args: f"role:{name}",
            mcp_handler=lambda name, args: {"mcp_result": name, "args": args},
            skill_handler=lambda name, args: f"skill:{name}:{args}",
            collection_handler=lambda name, args: [name],
            role=UNRESTRICTED_ROLE,
        )

    @pytest.mark.asyncio
    async def test_role_handler_called(self):
        d = self._make_dispatcher()
        result = await d.dispatch(ToolCall(kind="role", name="planner", args={}))
        assert result.ok is True
        assert result.output == "role:planner"
        assert result.kind == "role"
        assert result.name == "planner"

    @pytest.mark.asyncio
    async def test_mcp_handler_called_with_args(self):
        d = self._make_dispatcher()
        result = await d.dispatch(ToolCall(kind="mcp", name="fs", args={"op": "read"}))
        assert result.ok is True
        assert result.output == {"mcp_result": "fs", "args": {"op": "read"}}

    @pytest.mark.asyncio
    async def test_skill_handler_called(self):
        d = self._make_dispatcher()
        result = await d.dispatch(ToolCall(kind="skill", name="summarise", args={"q": 1}))
        assert result.ok is True
        assert "summarise" in result.output

    @pytest.mark.asyncio
    async def test_collection_handler_called(self):
        d = self._make_dispatcher()
        result = await d.dispatch(ToolCall(kind="collection", name="tasks", args={}))
        assert result.ok is True
        assert result.output == ["tasks"]


# ---------------------------------------------------------------------------
# DynamicDispatcher — unknown kind fail-closed
# ---------------------------------------------------------------------------

class TestDynamicDispatcherUnknownKind:
    @pytest.mark.asyncio
    async def test_unknown_kind_returns_error_result(self):
        d = DynamicDispatcher(
            role_handler=lambda n, a: "ok", role=UNRESTRICTED_ROLE
        )
        result = await d.dispatch(ToolCall(kind="unknown_xyz", name="foo", args={}))  # type: ignore[arg-type]
        assert result.ok is False
        assert result.error is not None
        assert "unknown_kind" in result.error

    @pytest.mark.asyncio
    async def test_no_handlers_at_all_fail_closed(self):
        d = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = await d.dispatch(ToolCall(kind="role", name="planner", args={}))
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_kind_not_registered_fail_closed(self):
        """Dispatcher has some handlers but the specific kind is absent."""
        d = DynamicDispatcher(
            skill_handler=lambda n, a: "ok", role=UNRESTRICTED_ROLE
        )
        result = await d.dispatch(ToolCall(kind="mcp", name="tool", args={}))
        assert result.ok is False
        assert "mcp" in (result.error or "")

    @pytest.mark.asyncio
    async def test_error_result_has_name_and_kind(self):
        d = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = await d.dispatch(ToolCall(kind="role", name="my-tool", args={}))
        assert result.name == "my-tool"
        assert result.kind == "role"


# ---------------------------------------------------------------------------
# DynamicDispatcher — handler raises exception → fail-closed
# ---------------------------------------------------------------------------

class TestDynamicDispatcherHandlerError:
    @pytest.mark.asyncio
    async def test_handler_exception_gives_error_result(self):
        def _bad_handler(name: str, args: dict) -> str:
            raise RuntimeError("boom")

        d = DynamicDispatcher(role_handler=_bad_handler, role=UNRESTRICTED_ROLE)
        result = await d.dispatch(ToolCall(kind="role", name="bad", args={}))
        assert result.ok is False
        assert "boom" in (result.error or "")

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_raise(self):
        """dispatch() must never propagate handler exceptions."""
        def _explode(name: str, args: dict) -> str:
            raise ValueError("explode")

        d = DynamicDispatcher(mcp_handler=_explode, role=UNRESTRICTED_ROLE)
        # Should not raise:
        result = await d.dispatch(ToolCall(kind="mcp", name="x", args={}))
        assert result.ok is False


# ---------------------------------------------------------------------------
# DynamicDispatcher — dispatch_all aggregation
# ---------------------------------------------------------------------------

class TestDynamicDispatcherDispatchAll:
    @pytest.mark.asyncio
    async def test_dispatch_all_returns_list_same_length(self):
        d = DynamicDispatcher(
            role_handler=lambda n, a: "ok",
            skill_handler=lambda n, a: "skill-ok",
            role=UNRESTRICTED_ROLE,
        )
        calls = [
            ToolCall(kind="role", name="planner"),
            ToolCall(kind="skill", name="scan"),
            ToolCall(kind="mcp", name="missing"),  # no mcp handler → fail-closed
        ]
        results = await d.dispatch_all(calls)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_dispatch_all_mixed_ok_and_fail(self):
        d = DynamicDispatcher(
            role_handler=lambda n, a: "ok", role=UNRESTRICTED_ROLE
        )
        calls = [
            ToolCall(kind="role", name="a"),
            ToolCall(kind="mcp", name="b"),   # unknown → fail
        ]
        results = await d.dispatch_all(calls)
        assert results[0].ok is True
        assert results[1].ok is False

    @pytest.mark.asyncio
    async def test_dispatch_all_empty_list(self):
        d = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        assert await d.dispatch_all([]) == []

    @pytest.mark.asyncio
    async def test_dispatch_all_order_preserved(self):
        order: list[str] = []

        def _handler(name: str, args: dict) -> str:
            order.append(name)
            return name

        d = DynamicDispatcher(role_handler=_handler, role=UNRESTRICTED_ROLE)
        calls = [ToolCall(kind="role", name=n) for n in ["a", "b", "c"]]
        await d.dispatch_all(calls)
        assert order == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# DispatchResult.to_dict
# ---------------------------------------------------------------------------

class TestDispatchResultToDict:
    def test_to_dict_ok(self):
        r = DispatchResult(ok=True, kind="role", name="x", output={"a": 1})
        d = r.to_dict()
        assert d["ok"] is True
        assert d["output"] == {"a": 1}
        assert d["error"] is None

    def test_to_dict_error(self):
        r = DispatchResult(ok=False, kind="mcp", name="y", error="oops")
        d = r.to_dict()
        assert d["ok"] is False
        assert d["error"] == "oops"
