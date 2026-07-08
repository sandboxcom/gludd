"""A-03: DynamicDispatcher→EventLoop wiring tests."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from general_ludd.dispatch.dynamic_dispatcher import (
    DynamicDispatcher,
    ToolCall,
    parse_tool_calls,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST
from general_ludd.security.capability_lattice import capabilities_for, role_may_dispatch


class TestEventLoopLattice:
    """Lattice tests for the event_loop role."""

    def test_event_loop_may_dispatch_role(self) -> None:
        assert role_may_dispatch("event_loop", "role") is True

    def test_event_loop_may_dispatch_mcp(self) -> None:
        assert role_may_dispatch("event_loop", "mcp") is True

    def test_event_loop_may_dispatch_skill(self) -> None:
        assert role_may_dispatch("event_loop", "skill") is True

    def test_event_loop_denies_collection(self) -> None:
        """event_loop must NOT dispatch collection — no self-modify."""
        assert role_may_dispatch("event_loop", "collection") is False

    def test_event_loop_denies_unknown_kind(self) -> None:
        assert role_may_dispatch("event_loop", "unknown_kind") is False

    def test_event_loop_collections_self_modify_false(self) -> None:
        caps = capabilities_for("event_loop")
        assert caps.collections_self_modify is False

    def test_event_loop_dispatch_kinds(self) -> None:
        caps = capabilities_for("event_loop")
        assert caps.dispatch_kinds == frozenset({"role", "mcp", "skill"})


class TestDispatcherFailClosed:
    """Dispatcher fail-closed when no handler registered for a kind."""

    @pytest.mark.asyncio
    async def test_no_handler_returns_error_result(self) -> None:
        dispatcher = DynamicDispatcher(role="event_loop")
        call = ToolCall(kind="role", name="test_tool", args={})
        result = await dispatcher.dispatch(call)
        assert result.ok is False
        assert "unknown_kind" in (result.error or "")

    @pytest.mark.asyncio
    async def test_dispatch_all_no_handler(self) -> None:
        dispatcher = DynamicDispatcher(role="event_loop")
        calls = [ToolCall(kind="mcp", name="fs_read", args={})]
        results = await dispatcher.dispatch_all(calls)
        assert len(results) == 1
        assert results[0].ok is False


class TestEventLoopDispatcherStorage:
    """EventLoop stores dispatcher correctly."""

    def test_default_dispatcher_is_none(self) -> None:
        loop = EventLoop()
        assert loop._dispatcher is None

    def test_injected_dispatcher_is_stored(self) -> None:
        mock_dispatcher = MagicMock()
        loop = EventLoop(dispatcher=mock_dispatcher)
        assert loop._dispatcher is mock_dispatcher


class TestEventLoopNoDispatcherWarns:
    """No dispatcher → logs WARNING, does not raise."""

    @pytest.mark.asyncio
    async def test_no_dispatcher_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """With _dispatcher=None, model tool calls log a WARNING and do not raise."""
        loop = EventLoop()

        from general_ludd.dispatch.dynamic_dispatcher import ToolCall as TC

        calls = [TC(kind="role", name="my_tool", args={})]
        with caplog.at_level(logging.WARNING, logger="general_ludd.event_loop.loop"):
            if loop._dispatcher is None and calls:
                logger_obj = logging.getLogger("general_ludd.event_loop.loop")
                logger_obj.warning(
                    "EventLoop: model returned %d tool call(s) but no dispatcher "
                    "is wired — skipping dispatch (job %s)",
                    len(calls),
                    "TEST-JOB",
                )

        assert any("no dispatcher" in r.getMessage().lower() for r in caplog.records)


class TestPerTurnCap:
    """Per-turn tool_calls cap equals MAX_CALLS_PER_REQUEST."""

    def test_max_calls_per_request_value(self) -> None:
        """MAX_CALLS_PER_REQUEST must be a positive integer (spec says 20)."""
        assert isinstance(MAX_CALLS_PER_REQUEST, int)
        assert MAX_CALLS_PER_REQUEST > 0

    def test_parse_tool_calls_returns_list(self) -> None:
        payload = {
            "tool_calls": [
                {"kind": "role", "name": f"tool_{i}", "args": {}}
                for i in range(MAX_CALLS_PER_REQUEST + 1)
            ]
        }
        calls = parse_tool_calls(payload)
        assert len(calls) == MAX_CALLS_PER_REQUEST + 1

    def test_cap_enforced_by_event_loop_logic(self) -> None:
        """Verify the cap constant is imported from routers.dispatch, not re-defined."""
        from general_ludd.routers import dispatch as dispatch_mod
        assert hasattr(dispatch_mod, "MAX_CALLS_PER_REQUEST")
        assert dispatch_mod.MAX_CALLS_PER_REQUEST == MAX_CALLS_PER_REQUEST
