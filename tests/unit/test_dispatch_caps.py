"""Tests for dispatch per-request call cap and name/kind truncation.

FIX 1: POST /api/dispatch with > MAX_CALLS_PER_REQUEST tool_calls -> 422.
FIX 2: A 1000-char tool name is truncated to 256 chars in the parsed ToolCall
        (which flows into DispatchResult name/error strings and log lines).
"""

from __future__ import annotations

import pytest

from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE, parse_tool_calls
from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST, register

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(role: str = UNRESTRICTED_ROLE):
    """Build a minimal FastAPI app with the dispatch router registered."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    register(
        app,
        {},
        role_handler=lambda name, args: f"role:{name}",
        mcp_handler=lambda name, args: f"mcp:{name}",
        skill_handler=lambda name, args: f"skill:{name}",
        collection_handler=lambda name, args: f"coll:{name}",
        role=role,
    )
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# FIX 1: per-request call cap
# ---------------------------------------------------------------------------

class TestCallCap:
    def test_exactly_max_calls_allowed(self):
        """MAX_CALLS_PER_REQUEST tool_calls should succeed (200)."""
        client = _make_client()
        calls = [{"kind": "role", "name": f"tool_{i}"} for i in range(MAX_CALLS_PER_REQUEST)]
        resp = client.post("/api/dispatch", json={"tool_calls": calls})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == MAX_CALLS_PER_REQUEST

    def test_one_over_max_returns_422(self):
        """MAX_CALLS_PER_REQUEST + 1 tool_calls must be rejected with 422."""
        client = _make_client()
        calls = [{"kind": "role", "name": f"tool_{i}"} for i in range(MAX_CALLS_PER_REQUEST + 1)]
        resp = client.post("/api/dispatch", json={"tool_calls": calls})
        assert resp.status_code == 422

    def test_422_detail_mentions_max(self):
        """The 422 error detail should reference the cap value."""
        client = _make_client()
        calls = [{"kind": "role", "name": f"t{i}"} for i in range(MAX_CALLS_PER_REQUEST + 1)]
        resp = client.post("/api/dispatch", json={"tool_calls": calls})
        detail = resp.json().get("detail", "")
        assert "20" in detail or "max" in detail.lower() or "too many" in detail.lower()

    def test_single_call_not_affected_by_cap(self):
        """A single-dict call (not tool_calls list) is always within cap."""
        client = _make_client()
        resp = client.post("/api/dispatch", json={"kind": "role", "name": "planner"})
        assert resp.status_code == 200

    def test_max_calls_constant_value(self):
        """Sanity: the constant should be 20."""
        assert MAX_CALLS_PER_REQUEST == 20


# ---------------------------------------------------------------------------
# FIX 2: name/kind truncation in _parse_single
# ---------------------------------------------------------------------------

class TestNameKindTruncation:
    def test_long_name_truncated_to_256(self):
        """A name longer than 256 chars is silently truncated at parse time."""
        long_name = "x" * 1000
        calls = parse_tool_calls({"kind": "role", "name": long_name})
        assert len(calls) == 1
        assert len(calls[0].name) == 256
        assert calls[0].name == "x" * 256

    def test_long_kind_truncated_to_64(self):
        """A kind longer than 64 chars is silently truncated at parse time."""
        long_kind = "k" * 200
        calls = parse_tool_calls({"kind": long_kind, "name": "tool"})
        assert len(calls) == 1
        assert len(calls[0].kind) == 64

    def test_short_name_unchanged(self):
        """Names within the limit pass through unchanged."""
        calls = parse_tool_calls({"kind": "role", "name": "planner"})
        assert calls[0].name == "planner"

    def test_short_kind_unchanged(self):
        """Kinds within the limit pass through unchanged."""
        calls = parse_tool_calls({"kind": "mcp", "name": "fs"})
        assert calls[0].kind == "mcp"

    @pytest.mark.asyncio
    async def test_truncated_name_flows_into_dispatch_result(self):
        """The truncated name appears in the DispatchResult, not the raw 1000-char string."""
        from general_ludd.dispatch.dynamic_dispatcher import DynamicDispatcher

        long_name = "n" * 1000
        # parse_tool_calls truncates the name to 256
        calls = parse_tool_calls({"kind": "role", "name": long_name})
        assert len(calls) == 1
        truncated = calls[0].name
        assert len(truncated) == 256

        # Dispatch the parsed call — result.name must also be the truncated form
        d = DynamicDispatcher(role=UNRESTRICTED_ROLE)
        result = await d.dispatch(calls[0])
        # Even in error (no handler registered) the name field is bounded
        assert len(result.name) == 256
        assert result.name == truncated

    def test_truncated_name_in_tool_calls_list(self):
        """Truncation applies to items inside the tool_calls list shape too."""
        long_name = "z" * 500
        calls = parse_tool_calls({
            "tool_calls": [
                {"kind": "skill", "name": long_name},
            ]
        })
        assert len(calls) == 1
        assert len(calls[0].name) == 256
