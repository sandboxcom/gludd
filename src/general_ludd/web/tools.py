"""MCP tool registration + an in-process dispatch handler for the web toolkit.

``register_web_tools(registry, ...)`` advertises five model-callable tools through
the LIVE MCP path (:meth:`MCPToolRegistry.register_tool`), each with a JSON
``input_schema`` so a tool loop can validate args and so the registry capability
gate + per-tool wait_for apply automatically.  ``web_handler`` is the secondary
in-process route (kind ``"web"``) returning ``.model_dump()`` JSON dicts.
"""

from __future__ import annotations

from typing import Any

from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.web.crawl import Crawler, CrawlPolicy
from general_ludd.web.fetch import fetch_raw
from general_ludd.web.parse import fetch_parsed
from general_ludd.web.render import RenderConfig, render_page
from general_ludd.web.results import RenderResult, WebError

WEB_SERVER_ID = "web"

_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "web_fetch",
        "description": "SSRF-hardened raw HTTP(S) fetch. Returns ok/status/headers/body/final_url.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "https URL to fetch"},
                "method": {"type": "string", "default": "GET"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "web_fetch_parsed",
        "description": "Fetch + parse a page (title/text/links/meta) via the stdlib HTML parser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "web_crawl",
        "description": "Polite same-domain BFS crawl (robots, rate-limit, depth/page caps, SSRF per hop).",
        "input_schema": {
            "type": "object",
            "properties": {
                "seed_urls": {"type": "array", "items": {"type": "string"}},
                "max_pages": {"type": "integer", "default": 50},
                "max_depth": {"type": "integer", "default": 3},
            },
            "required": ["seed_urls"],
        },
    },
    {
        "name": "web_render",
        "description": (
            "Optional JS render (Playwright/Selenium; headless_local/remote_cdp/"
            "webdriver_remote). Structured renderer_unavailable fallback."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["headless_local", "remote_cdp", "webdriver_remote"],
                    "default": "headless_local",
                },
                "endpoint": {"type": "string"},
            },
            "required": ["url"],
        },
    },
]


def register_web_tools(
    registry: MCPToolRegistry, *, server_id: str = WEB_SERVER_ID
) -> list[str]:
    """Register the web tools on ``registry`` and return their names."""
    names: list[str] = []
    for spec in _TOOL_SPECS:
        registry.register_tool(
            server_id,
            MCPTool(
                name=spec["name"],
                description=spec["description"],
                input_schema=spec["input_schema"],
            ),
        )
        names.append(spec["name"])
    return names


def call_web_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Backend for a web tool call — returns a JSON-serialisable ``.model_dump()``.

    Never raises: an unknown tool / bad args yields a structured error dict.
    """
    args = args or {}
    if name == "web_fetch":
        url = str(args.get("url", ""))
        return fetch_raw(url, method=str(args.get("method", "GET"))).model_dump()
    if name == "web_fetch_parsed":
        return fetch_parsed(str(args.get("url", ""))).model_dump()
    if name == "web_crawl":
        seeds = args.get("seed_urls") or []
        if not isinstance(seeds, list):
            seeds = [str(seeds)]
        policy = CrawlPolicy(
            max_pages=int(args.get("max_pages", 50)),
            max_depth=int(args.get("max_depth", 3)),
        )
        return Crawler([str(s) for s in seeds], policy=policy).crawl().model_dump()
    if name == "web_render":
        cfg = RenderConfig(
            mode=str(args.get("mode", "headless_local")),
            endpoint=args.get("endpoint"),
            remote_host_allowlist=list(args.get("remote_host_allowlist", []) or []),
        )
        return render_page(str(args.get("url", "")), config=cfg).model_dump()
    return RenderResult(
        ok=False, error=WebError.NO_PROVIDER, detail=f"unknown web tool {name!r}"
    ).model_dump()


# In-process dispatch handler (kind "web"): a thin (name, args) -> JSON adapter
# matching DynamicDispatcher's Handler signature.
def web_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return call_web_tool(name, args)
