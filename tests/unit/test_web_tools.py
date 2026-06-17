"""Offline tests for web tool registration + in-process dispatch wiring."""

from __future__ import annotations

import socket

import pytest

from general_ludd.dispatch.dynamic_dispatcher import (
    PRIVILEGED_KINDS,
    DynamicDispatcher,
    ToolCall,
)
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.security.capability_lattice import role_may_dispatch
from general_ludd.web import ssrf_client as sc
from general_ludd.web.tools import call_web_tool, register_web_tools, web_handler

_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    def _fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _fake)


def test_register_advertises_tools():
    reg = MCPToolRegistry()
    names = register_web_tools(reg)
    assert "web_fetch" in names
    assert "web_crawl" in names
    for n in names:
        tool = reg.get_tool(n)
        assert tool is not None
        assert tool.server_id == "web"
        assert tool.input_schema.get("type") == "object"


def test_call_web_tool_unknown_structured():
    out = call_web_tool("nope", {})
    assert out["ok"] is False


def test_web_kind_is_privileged():
    assert "web" in PRIVILEGED_KINDS


def test_none_role_denied_web_dispatch():
    d = DynamicDispatcher(web_handler=web_handler, role=None)
    res = d.dispatch(ToolCall(kind="web", name="web_fetch", args={"url": "https://x.example.com/"}))
    assert res.ok is False
    assert "capability_denied" in (res.error or "")


def test_coder_role_may_dispatch_web():
    assert role_may_dispatch("coder", "web") is True
    assert role_may_dispatch(None, "web") is False


def test_web_handler_dispatch_ok_with_role(monkeypatch):
    # An SSRF-blocked literal still returns a structured (ok=true dispatch, ok=false payload).
    d = DynamicDispatcher(web_handler=web_handler, role="coder")
    res = d.dispatch(ToolCall(kind="web", name="web_fetch", args={"url": "https://127.0.0.1/x"}))
    assert res.ok is True  # handler ran (did not raise)
    assert res.output["ok"] is False
    assert res.output["error"] == "ssrf_blocked"
