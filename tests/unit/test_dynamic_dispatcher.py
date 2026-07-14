"""Structural tests for src/general_ludd/dispatch/dynamic_dispatcher.py."""

from __future__ import annotations

import inspect
import json

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

# ── ToolCall ───────────────────────────────────────────────────────────


def test_toolcall_instantiation_with_all_fields() -> None:
    tc = ToolCall(kind="role", name="run_playbook", args={"playbook": "deploy.yml"})
    assert tc.kind == "role"
    assert tc.name == "run_playbook"
    assert tc.args == {"playbook": "deploy.yml"}


def test_toolcall_default_args_is_empty_dict() -> None:
    tc = ToolCall(kind="skill", name="revealjs")
    assert tc.args == {}


def test_toolcall_kind_accepts_literal_values() -> None:
    for kind in ("role", "collection", "mcp", "skill"):
        tc = ToolCall(kind=kind, name="x")  # type: ignore[arg-type]
        assert tc.kind == kind


# ── DispatchResult + to_dict ───────────────────────────────────────────


def test_dispatchresult_success_to_dict() -> None:
    dr = DispatchResult(ok=True, kind="role", name="run_playbook", output={"status": "ok"})
    d = dr.to_dict()
    assert d == {"ok": True, "kind": "role", "name": "run_playbook", "output": {"status": "ok"}, "error": None}


def test_dispatchresult_error_to_dict() -> None:
    dr = DispatchResult(ok=False, kind="mcp", name="bad_tool", error="handler_error")
    d = dr.to_dict()
    assert d["ok"] is False
    assert d["error"] == "handler_error"
    assert d["output"] is None


def test_dispatchresult_default_fields_are_empty() -> None:
    dr = DispatchResult(ok=False)
    assert dr.kind == ""
    assert dr.name == ""
    assert dr.output is None
    assert dr.error is None


def test_dispatchresult_to_dict_includes_all_keys() -> None:
    dr = DispatchResult(ok=True, kind="collection", name="ping", output="pong")
    d = dr.to_dict()
    assert set(d.keys()) == {"ok", "kind", "name", "output", "error"}


# ── parse_tool_calls — dict input ─────────────────────────────────────


def test_parse_tool_calls_dict_with_tool_calls_list() -> None:
    raw = {"tool_calls": [{"kind": "role", "name": "run", "args": {"x": 1}}]}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].kind == "role"
    assert result[0].name == "run"
    assert result[0].args == {"x": 1}


def test_parse_tool_calls_dict_single_call() -> None:
    raw = {"kind": "mcp", "name": "fetch"}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].kind == "mcp"
    assert result[0].name == "fetch"


def test_parse_tool_calls_dict_with_multiple_tool_calls() -> None:
    raw = {
        "tool_calls": [
            {"kind": "role", "name": "a"},
            {"kind": "mcp", "name": "b"},
        ]
    }
    result = parse_tool_calls(raw)
    assert len(result) == 2
    assert [c.name for c in result] == ["a", "b"]


def test_parse_tool_calls_skips_non_dict_items_in_list() -> None:
    raw = {"tool_calls": ["not-a-dict", 42, {"kind": "role", "name": "valid"}]}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].name == "valid"


def test_parse_tool_calls_skips_items_without_name() -> None:
    raw = {"tool_calls": [{"kind": "role"}, {"kind": "mcp", "name": ""}]}
    result = parse_tool_calls(raw)
    assert len(result) == 0


def test_parse_tool_calls_missing_kind_defaults_to_unknown() -> None:
    raw = {"tool_calls": [{"name": "test"}]}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].kind == "unknown"
    assert result[0].name == "test"


def test_parse_tool_calls_kind_none_defaults_to_unknown() -> None:
    raw = {"tool_calls": [{"kind": None, "name": "test"}]}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].kind == "unknown"


def test_parse_tool_calls_tool_calls_not_a_list_returns_empty() -> None:
    result = parse_tool_calls({"tool_calls": "not-a-list"})
    assert result == []


def test_parse_tool_calls_dict_without_kind_or_tool_calls() -> None:
    result = parse_tool_calls({"unrelated": 1})
    assert result == []


# ── parse_tool_calls — JSON string input ──────────────────────────────


def test_parse_tool_calls_json_string_with_tool_calls_list() -> None:
    raw = json.dumps({"tool_calls": [{"kind": "skill", "name": "render"}]})
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].kind == "skill"


def test_parse_tool_calls_json_string_single_call() -> None:
    raw = json.dumps({"kind": "collection", "name": "ping"})
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].kind == "collection"


def test_parse_tool_calls_invalid_json_returns_empty() -> None:
    result = parse_tool_calls("{not valid json}")
    assert result == []


def test_parse_tool_calls_empty_string_returns_empty() -> None:
    result = parse_tool_calls("")
    assert result == []


def test_parse_tool_calls_json_that_is_not_a_dict() -> None:
    result = parse_tool_calls("42")
    assert result == []
    result2 = parse_tool_calls("[1, 2, 3]")
    assert result2 == []


def test_parse_tool_calls_json_string_with_extra_whitespace() -> None:
    raw = '  \n  {"tool_calls": [{"kind": "mcp", "name": "tool"}]}  \n '
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].name == "tool"


# ── parse_tool_calls — edge cases ─────────────────────────────────────


def test_parse_tool_calls_handles_name_truncation() -> None:
    long_name = "x" * 300
    raw = {"tool_calls": [{"kind": "role", "name": long_name}]}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert len(result[0].name) == 256
    assert result[0].name == long_name[:256]


def test_parse_tool_calls_handles_kind_truncation() -> None:
    long_kind = "x" * 100
    raw = {"tool_calls": [{"kind": long_kind, "name": "test"}]}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert len(result[0].kind) == 64
    assert result[0].kind == long_kind[:64]


def test_parse_tool_calls_args_not_a_dict_defaults_to_empty() -> None:
    raw = {"tool_calls": [{"kind": "role", "name": "test", "args": "not-a-dict"}]}
    result = parse_tool_calls(raw)
    assert len(result) == 1
    assert result[0].args == {}


def test_parse_tool_calls_missing_args_defaults_to_empty_dict() -> None:
    raw = {"tool_calls": [{"kind": "role", "name": "test"}]}
    result = parse_tool_calls(raw)
    assert result[0].args == {}


def test_parse_tool_calls_name_not_a_string_returns_empty() -> None:
    raw = {"tool_calls": [{"kind": "role", "name": 42}]}
    result = parse_tool_calls(raw)
    assert result == []


# ── UNRESTRICTED_ROLE ──────────────────────────────────────────────────


def test_unrestricted_role_is_an_object_instance() -> None:
    assert isinstance(UNRESTRICTED_ROLE, object)
    assert type(UNRESTRICTED_ROLE) is object


def test_unrestricted_role_is_identity_sentinel() -> None:
    assert UNRESTRICTED_ROLE is UNRESTRICTED_ROLE


def test_unrestricted_role_is_not_another_object_instance() -> None:
    another = object()
    assert UNRESTRICTED_ROLE is not another


def test_unrestricted_role_is_not_none() -> None:
    assert UNRESTRICTED_ROLE is not None


def test_unrestricted_role_is_not_a_string() -> None:
    assert not isinstance(UNRESTRICTED_ROLE, str)


def test_unrestricted_role_equality_would_not_work_with_forgery() -> None:
    forged = "__unrestricted__"
    assert forged != UNRESTRICTED_ROLE
    assert UNRESTRICTED_ROLE is not forged


# ── PRIVILEGED_KINDS ──────────────────────────────────────────────────


def test_privileged_kinds_is_a_frozenset() -> None:
    assert isinstance(PRIVILEGED_KINDS, frozenset)


def test_privileged_kinds_contains_expected_values() -> None:
    assert "role" in PRIVILEGED_KINDS
    assert "collection" in PRIVILEGED_KINDS
    assert "mcp" in PRIVILEGED_KINDS
    assert "skill" in PRIVILEGED_KINDS


def test_privileged_kinds_length_is_four() -> None:
    assert len(PRIVILEGED_KINDS) == 4


def test_privileged_kinds_does_not_contain_unknown_value() -> None:
    assert "unknown" not in PRIVILEGED_KINDS
    assert "" not in PRIVILEGED_KINDS


# ── structured_tool_calls_to_calls ────────────────────────────────────


def test_structured_tool_calls_to_calls_valid() -> None:
    raw = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
        }
    ]
    result = structured_tool_calls_to_calls(raw)
    assert len(result) == 1
    assert result[0].kind == "mcp"
    assert result[0].name == "get_weather"
    assert result[0].args == {"city": "NYC"}


def test_structured_tool_calls_to_calls_arguments_already_dict() -> None:
    raw = [{"id": "c1", "type": "function", "function": {"name": "calc", "arguments": {"a": 1}}}]
    result = structured_tool_calls_to_calls(raw)
    assert result[0].args == {"a": 1}


def test_structured_tool_calls_to_calls_invalid_json_arguments() -> None:
    raw = [{"id": "c1", "function": {"name": "test", "arguments": "{bad"}}]
    result = structured_tool_calls_to_calls(raw)
    assert result[0].args == {}


def test_structured_tool_calls_to_calls_none_input_returns_empty() -> None:
    assert structured_tool_calls_to_calls(None) == []


def test_structured_tool_calls_to_calls_empty_list_returns_empty() -> None:
    assert structured_tool_calls_to_calls([]) == []


def test_structured_tool_calls_to_calls_skips_non_dict_items() -> None:
    raw: list[dict[str, object] | None] = [
        "not-a-dict",  # type: ignore[list-item]
        {"id": "c1", "function": {"name": "valid"}},
    ]
    result = structured_tool_calls_to_calls(raw)  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0].name == "valid"


def test_structured_tool_calls_to_calls_missing_function_field() -> None:
    raw = [{"id": "c1"}]
    result = structured_tool_calls_to_calls(raw)
    assert result == []


def test_structured_tool_calls_to_calls_function_not_a_dict() -> None:
    raw = [{"id": "c1", "function": "not-a-dict"}]
    result = structured_tool_calls_to_calls(raw)
    assert result == []


def test_structured_tool_calls_to_calls_missing_name_skipped() -> None:
    raw = [{"id": "c1", "function": {"arguments": "{}"}}]
    result = structured_tool_calls_to_calls(raw)
    assert result == []


def test_structured_tool_calls_to_calls_name_truncation() -> None:
    long_name = "x" * 300
    raw = [{"id": "c1", "function": {"name": long_name, "arguments": "{}"}}]
    result = structured_tool_calls_to_calls(raw)
    assert len(result[0].name) == 256


# ── DynamicDispatcher ─────────────────────────────────────────────────


def test_dispatcher_handlers_registered_via_init() -> None:
    def handler(_name: str, _args: dict) -> str:
        return "ok"

    dd = DynamicDispatcher(role_handler=handler)
    assert "role" in dd._handlers
    assert dd._handlers["role"] is handler


def test_dispatcher_handlers_none_means_no_handler() -> None:
    dd = DynamicDispatcher()
    assert "role" not in dd._handlers
    assert "mcp" not in dd._handlers


@pytest.mark.asyncio
async def test_dispatcher_dispatch_unknown_kind_fails_closed() -> None:
    dd = DynamicDispatcher()
    call = ToolCall(kind="nonexistent", name="bad")  # type: ignore[arg-type]
    result = await dd.dispatch(call)
    assert result.ok is False
    assert result.error == "unknown_kind:nonexistent"


@pytest.mark.asyncio
async def test_dispatcher_dispatch_known_kind_with_handler() -> None:
    def handler(_name: str, _args: dict) -> str:
        return "result_string"

    dd = DynamicDispatcher(role_handler=handler, role=UNRESTRICTED_ROLE)
    call = ToolCall(kind="role", name="test")
    result = await dd.dispatch(call)
    assert result.ok is True
    assert result.output == "result_string"


@pytest.mark.asyncio
async def test_dispatcher_dispatch_handler_exception_caught() -> None:
    def handler(_name: str, _args: dict) -> str:
        raise RuntimeError("boom")

    dd = DynamicDispatcher(role_handler=handler, role=UNRESTRICTED_ROLE)
    call = ToolCall(kind="role", name="test")
    result = await dd.dispatch(call)
    assert result.ok is False
    assert result.error == "handler_error"


@pytest.mark.asyncio
async def test_dispatcher_dispatch_async_handler() -> None:
    async def handler(_name: str, _args: dict) -> str:
        return "async_result"

    dd = DynamicDispatcher(skill_handler=handler, role=UNRESTRICTED_ROLE)
    call = ToolCall(kind="skill", name="test")
    result = await dd.dispatch(call)
    assert result.ok is True
    assert result.output == "async_result"


@pytest.mark.asyncio
async def test_dispatcher_dispatch_all_accumulates_results() -> None:
    call_history: list[str] = []

    def handler(name: str, _args: dict) -> str:
        call_history.append(name)
        return name

    dd = DynamicDispatcher(role_handler=handler, role=UNRESTRICTED_ROLE)
    calls = [ToolCall(kind="role", name="a"), ToolCall(kind="role", name="b")]
    results = await dd.dispatch_all(calls)
    assert call_history == ["a", "b"]
    assert len(results) == 2
    assert all(r.ok for r in results)


@pytest.mark.asyncio
async def test_dispatcher_role_none_denies_privileged_kinds() -> None:
    def handler(_name: str, _args: dict) -> str:
        return "should_not_run"

    dd = DynamicDispatcher(role_handler=handler, role=None)
    call = ToolCall(kind="role", name="test")
    result = await dd.dispatch(call)
    assert result.ok is False
    assert result.error == "capability_denied"


@pytest.mark.asyncio
async def test_dispatcher_unrestricted_role_bypasses_gate() -> None:
    def handler(_name: str, _args: dict) -> str:
        return "bypassed"

    dd = DynamicDispatcher(role_handler=handler, role=UNRESTRICTED_ROLE)
    call = ToolCall(kind="role", name="test")
    result = await dd.dispatch(call)
    assert result.ok is True
    assert result.output == "bypassed"


@pytest.mark.asyncio
async def test_dispatcher_unrestricted_role_uses_identity_check() -> None:
    def handler(_name: str, _args: dict) -> str:
        return "only_with_sentinel"

    dd = DynamicDispatcher(role_handler=handler, role=UNRESTRICTED_ROLE)
    sentinel_copy = UNRESTRICTED_ROLE
    dd._role = sentinel_copy
    call = ToolCall(kind="role", name="test")
    result = await dd.dispatch(call)
    assert result.ok is True


@pytest.mark.asyncio
async def test_dispatcher_forged_role_string_blocked() -> None:
    def handler(_name: str, _args: dict) -> str:
        return "should_not_run"

    dd = DynamicDispatcher(role_handler=handler, role="__unrestricted__")
    call = ToolCall(kind="role", name="test")
    result = await dd.dispatch(call)
    assert result.ok is False


def test_dispatcher_list_available_returns_registered_kinds() -> None:
    dd = DynamicDispatcher(role_handler=lambda n, a: "ok", mcp_handler=lambda n, a: "ok")
    kinds = dd.list_available()
    assert "registered_kinds" in kinds
    assert set(kinds["registered_kinds"]) == {"role", "mcp"}


def test_dispatcher_list_available_empty_when_no_handlers() -> None:
    dd = DynamicDispatcher()
    assert dd.list_available() == {"registered_kinds": []}


def test_dispatcher_dispatch_is_async_function() -> None:
    assert inspect.iscoroutinefunction(DynamicDispatcher.dispatch)
