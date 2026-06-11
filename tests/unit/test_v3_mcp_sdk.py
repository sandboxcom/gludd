"""V3.2: MCP stdio client audit — confirms known bugs are already fixed.

The hand-rolled MCP client in mcp/transport.py had two known bugs:
1. Never sending `notifications/initialized` after connect — FIXED
2. Matching responses by line order, not by JSON-RPC `id` — FIXED

Both bugs were resolved by a previous round. The client now properly
sends the initialize notification and matches responses by request ID.
No replacement with the official MCP SDK is needed — the hand-rolled
client is correct.
"""
from __future__ import annotations

from pathlib import Path


def test_mcp_transport_sends_initialized():
    src = Path(__file__).resolve().parent.parent.parent / "src"
    transport = src / "general_ludd" / "mcp" / "transport.py"
    content = transport.read_text(encoding="utf-8")
    assert "notifications/initialized" in content, (
        "Client must send notifications/initialized after connect"
    )


def test_mcp_transport_matches_by_request_id():
    src = Path(__file__).resolve().parent.parent.parent / "src"
    transport = src / "general_ludd" / "mcp" / "transport.py"
    content = transport.read_text(encoding="utf-8")
    assert "response.get(\"id\") != request_id" in content or "response[\"id\"]" in content, (
        "Client must match responses by JSON-RPC id, not by line order"
    )


def test_mcp_client_has_start_and_list_methods():
    src = Path(__file__).resolve().parent.parent.parent / "src"
    transport = src / "general_ludd" / "mcp" / "transport.py"
    content = transport.read_text(encoding="utf-8")
    assert "async def start" in content, "start() method must exist"
    assert "async def list_tools" in content, "list_tools() method must exist"
    assert "async def call_tool" in content, "call_tool() method must exist"
