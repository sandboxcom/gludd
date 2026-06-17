"""Tests for the web dispatch wiring: kind, deny-by-default lattice, handler."""

from __future__ import annotations

import httpx

from general_ludd.dispatch.dynamic_dispatcher import (
    PRIVILEGED_KINDS,
    DynamicDispatcher,
    ToolCall,
)
from general_ludd.security.capability_lattice import role_may_dispatch
from general_ludd.web.handler import make_web_handler
from general_ludd.web.policy import WebPolicy
from general_ludd.web.resilience import WebResilience
from general_ludd.web.safe_fetch import SafeFetcher
from general_ludd.web.types import WebError


def test_web_kind_is_privileged() -> None:
    assert "web" in PRIVILEGED_KINDS


def test_web_deny_by_default_lattice() -> None:
    # Only the operator role gets "web"; every other role is denied.
    assert role_may_dispatch("operator", "web") is True
    assert role_may_dispatch("coder", "web") is False
    assert role_may_dispatch("self_improve_agent", "web") is False
    assert role_may_dispatch("report_status", "web") is False
    assert role_may_dispatch(None, "web") is False
    assert role_may_dispatch("unknown-role", "web") is False


def test_unbound_role_denied_web_kind() -> None:
    # A None role must be denied "web" fail-closed (it is privileged).
    d = DynamicDispatcher(web_handler=lambda n, a: {"ran": True}, role=None)
    result = d.dispatch(ToolCall(kind="web", name="fetch_raw", args={"url": "https://e.com/"}))
    assert result.ok is False
    assert "capability_denied" in (result.error or "")


def test_operator_role_reaches_web_handler() -> None:
    d = DynamicDispatcher(web_handler=lambda n, a: {"ran": n}, role="operator")
    result = d.dispatch(ToolCall(kind="web", name="fetch_raw", args={"url": "https://e.com/"}))
    assert result.ok is True
    assert result.output == {"ran": "fetch_raw"}


def _kit(handler):
    policy = WebPolicy(max_attempts=1)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = SafeFetcher(client=client, resolver=lambda h, p: ["93.184.216.34"], policy=policy)
    res = WebResilience(policy)
    res._sleep = lambda _s: None
    return fetcher, res


def test_handler_returns_json_dict() -> None:
    # Inject an offline fetcher whose resolver forces a block so NO real network
    # is touched; assert the handler always returns a JSON dict and never raises.
    policy = WebPolicy(max_attempts=1)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                          follow_redirects=False)
    fetcher = SafeFetcher(client=client, resolver=lambda h, p: ["10.0.0.1"], policy=policy)
    handler = make_web_handler()
    out = handler("fetch_raw", {"url": "https://example.com/", "fetcher": fetcher})
    assert isinstance(out, dict)
    assert "ok" in out
    assert out["ok"] is False  # resolver forced an internal IP -> SSRF block


def test_handler_routes_with_injected_fetcher() -> None:
    fetcher, res = _kit(lambda r: httpx.Response(200, text="<p>hi</p>",
                                                 headers={"content-type": "text/html"}))
    handler = make_web_handler()
    out = handler("fetch_parsed", {"url": "https://example.com/",
                                   "fetcher": fetcher, "resilience": res})
    assert out["ok"] is True
    assert out["parsed"]["text"]


def test_handler_unknown_tool_structured() -> None:
    handler = make_web_handler()
    out = handler("nope", {"url": "https://example.com/"})
    assert out["ok"] is False
    assert out["error"] == WebError.PARSE_ERROR.value


def test_handler_missing_url_structured() -> None:
    handler = make_web_handler()
    out = handler("fetch_raw", {})
    assert out["ok"] is False
    assert out["error"] == WebError.PARSE_ERROR.value
