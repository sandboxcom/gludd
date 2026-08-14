"""In-process "builtin" MCP tools exposed to the model.

All model-callable tools flow through the MCP client path; there is no separate
first-party tool mechanism. To make gludd's own capabilities model-callable we
register a synthetic ``gludd-builtin`` server (see
:meth:`general_ludd.mcp.client.MCPClient.register_builtin`) whose tools are
backed by Python coroutines instead of a subprocess.

Slice 2 exposes :data:`RUN_PROJECT_CHECK_TOOL` — ``run_project_check`` — which
lets the agent run a *target project's* declared check (test/lint/build/…) via
:mod:`general_ludd.project_runner`, so gludd can work an external polyglot repo
rather than only self-hosting on its own ``make`` gates.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from pathlib import Path
from typing import Any

from general_ludd.mcp.registry import MCPTool
from general_ludd.project_runner import (
    ProjectCommandRunner,
    ProjectProfileError,
    load_project_profile,
)
from general_ludd.retrieval.web import WebRetriever
from general_ludd.web import WebError, WebResult, WebToolkit

logger = logging.getLogger(__name__)

# Server id under which every builtin tool is registered.
BUILTIN_SERVER_ID = "gludd-builtin"

RUN_PROJECT_CHECK_TOOL = MCPTool(
    name="run_project_check",
    description=(
        "Run a named check (e.g. 'test', 'lint', 'build', 'typecheck', 'sast') "
        "declared in the target project's project.yml, inside the jailed "
        "workspace. Returns the structured result: whether it passed, the exit "
        "code, duration, and tails of stdout/stderr. Use this to verify a "
        "target project's toolchain rather than gludd's own gates."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "check_name": {
                "type": "string",
                "description": (
                    "Logical check to run — must be a key in the project's "
                    "project.yml 'commands' map (test/lint/build/typecheck/…)."
                ),
            },
            "workspace": {
                "type": "string",
                "description": (
                    "Optional path to the target project root (where project.yml "
                    "lives). Defaults to the agent's workspace / current directory."
                ),
            },
        },
        "required": ["check_name"],
    },
)


# ---------------------------------------------------------------------------
# web_retrieve
# ---------------------------------------------------------------------------

WEB_RETRIEVE_TOOL = MCPTool(
    name="web_retrieve",
    description=(
        "Fetch a live web page by URL and return its status code, text "
        "content, page title, and response headers. Results are cached "
        "for 1 hour. The domain must be in the GLUDD_WEB_FETCH_ALLOWED_DOMAINS "
        "allowlist (comma-separated env var) when that variable is set."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL of the web page to fetch.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    "Override the default fetch timeout in seconds "
                    "(default 30)."
                ),
            },
        },
        "required": ["url"],
    },
)

WEB_FETCH_TOOL = MCPTool(
    name="web_fetch",
    description=(
        "Fetch one HTTPS resource through Gludd's DNS-pinned, redirect-checked, "
        "byte- and deadline-bounded outbound client."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The absolute HTTPS URL to fetch."},
            "method": {"type": "string", "enum": ["GET", "HEAD"], "default": "GET"},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
)

WEB_FETCH_PARSED_TOOL = MCPTool(
    name="web_fetch_parsed",
    description="Fetch and extract bounded visible text, metadata, headings, and links.",
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The absolute HTTPS URL."}},
        "required": ["url"],
        "additionalProperties": False,
    },
)

WEB_SEARCH_TOOL = MCPTool(
    name="web_search",
    description=(
        "Search through an operator-configured provider and securely gather a bounded "
        "set of partial page results."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A non-empty search query."},
            "top_n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
            "fetch_results": {"type": "boolean", "default": True},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)

WEB_CRAWL_TOOL = MCPTool(
    name="web_crawl",
    description=(
        "Run a robots-aware, same-host, sequential breadth-first crawl under hard "
        "deadline, page, depth, and link limits."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "seed_url": {"type": "string", "description": "The absolute HTTPS crawl seed."},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 100},
            "max_depth": {"type": "integer", "minimum": 0, "maximum": 5},
        },
        "required": ["seed_url"],
        "additionalProperties": False,
    },
)

WEB_RENDER_TOOL = MCPTool(
    name="web_render",
    description=(
        "Process securely prefetched HTML through an optional offline renderer; "
        "returns a structured unavailable result when disabled or absent."
    ),
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The absolute HTTPS URL."}},
        "required": ["url"],
        "additionalProperties": False,
    },
)

_WEB_TOOLS = (
    WEB_FETCH_TOOL,
    WEB_FETCH_PARSED_TOOL,
    WEB_SEARCH_TOOL,
    WEB_CRAWL_TOOL,
    WEB_RENDER_TOOL,
)


class BuiltinToolHandler:
    """Coroutine dispatcher backing the ``gludd-builtin`` synthetic server."""

    def __init__(
        self,
        default_workspace: str | Path | None = None,
        *,
        web_retriever: WebRetriever | None = None,
        web_toolkit: WebToolkit | None = None,
    ) -> None:
        """Initialize optional legacy retrieval and bounded web-toolkit seams."""
        self._default_workspace = default_workspace
        self._web_retriever = web_retriever
        self._web_toolkit = web_toolkit if web_toolkit is not None else WebToolkit()

    async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one registered builtin call to its bounded implementation."""
        if tool_name == RUN_PROJECT_CHECK_TOOL.name:
            return await self._run_project_check(arguments)
        if tool_name == WEB_RETRIEVE_TOOL.name:
            return await self._web_retrieve(arguments)
        if tool_name in {tool.name for tool in _WEB_TOOLS}:
            return await self._run_web_tool(tool_name, arguments)
        return {"error": f"unknown builtin tool: {tool_name!r}"}

    def _jail_root(self) -> Path:
        """Resolved base directory a model-supplied workspace must stay within.

        Precedence: the ``GLUDD_PROJECT_ROOT`` env override, else the daemon's
        configured ``default_workspace``, else the process cwd. This is the
        containment boundary for :meth:`_contain_workspace`.
        """
        env_root = os.environ.get("GLUDD_PROJECT_ROOT")
        base_raw: str | Path
        if env_root and env_root.strip():
            base_raw = env_root.strip()
        elif self._default_workspace is not None:
            base_raw = self._default_workspace
        else:
            base_raw = Path.cwd()
        return Path(base_raw).resolve()

    def _contain_workspace(self, candidate: str) -> Path | None:
        """Confine a MODEL-SUPPLIED workspace to the jail root (fail-closed).

        ``candidate`` is untrusted (the model can pass any string), so its
        realpath must resolve to the jail root or a descendant of it. A relative
        path is resolved *against the jail root*, not the process cwd. Returns
        the resolved path when contained, or ``None`` when it escapes the base
        (the caller then refuses the call with a data error).
        """
        base = self._jail_root()
        path = Path(candidate)
        if not path.is_absolute():
            path = base / path
        resolved = path.resolve()
        if resolved == base or resolved.is_relative_to(base):
            return resolved
        return None

    async def _run_project_check(self, arguments: dict[str, Any]) -> dict[str, Any]:
        check_name = arguments.get("check_name")
        if not isinstance(check_name, str) or not check_name.strip():
            return {"error": "run_project_check requires a non-empty 'check_name' string"}
        check_name = check_name.strip()

        workspace_arg = arguments.get("workspace")
        workspace: str | Path
        if isinstance(workspace_arg, str) and workspace_arg.strip():
            # The workspace is MODEL-SUPPLIED and thus untrusted: contain it to
            # the jail root so the model cannot point run_project_check at an
            # arbitrary host directory (``/``, another repo) that merely happens
            # to hold a project.yml. Fail CLOSED with a data error result, never
            # an exception that would abort the tool loop.
            contained = self._contain_workspace(workspace_arg.strip())
            if contained is None:
                return {
                    "error": (
                        "workspace escapes the allowed project root — refused. "
                        "run_project_check may only run inside the configured "
                        "jail root (GLUDD_PROJECT_ROOT / the agent workspace)."
                    ),
                    "check": check_name,
                }
            workspace = contained
        elif self._default_workspace is not None:
            workspace = self._default_workspace
        else:
            workspace = Path.cwd()

        # Fail-soft: a missing/invalid project.yml or unsafe command is a
        # configuration error the model should see as data, not an exception
        # that aborts the tool loop.
        try:
            profile = load_project_profile(workspace)
            runner = ProjectCommandRunner(workspace, profile)
        except ProjectProfileError as exc:
            return {"error": str(exc), "check": check_name}

        try:
            # runner.run is blocking (spawns + waits on a subprocess); run it off
            # the event loop so the async tool dispatch isn't stalled.
            result = await asyncio.to_thread(runner.run, check_name)
        except ProjectProfileError as exc:
            return {"error": str(exc), "check": check_name}

        return dataclasses.asdict(result)

    async def _web_retrieve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            return {"error": "web_retrieve requires a non-empty 'url' string"}
        url = url.strip()

        timeout_seconds = arguments.get("timeout_seconds", 30)
        try:
            timeout = int(timeout_seconds)
        except (TypeError, ValueError):
            timeout = 30

        if self._web_retriever is not None and timeout == self._web_retriever._timeout:
            retriever = self._web_retriever
        else:
            retriever = WebRetriever(timeout_seconds=timeout)
        try:
            result = retriever.fetch_web_page(url)
        except ValueError as exc:
            return {"error": str(exc), "url": url}

        return {
            "url": result.url,
            "status_code": result.status_code,
            "content": result.content,
            "title": result.title,
            "headers": result.headers,
        }

    @staticmethod
    def _web_argument_error(tool_name: str, detail: str, *, url: str = "") -> dict[str, Any]:
        return WebResult(
            ok=False,
            url=url,
            error=WebError.INVALID_INPUT,
            detail=f"{tool_name}: {detail}",
        ).model_dump(mode="json")

    @staticmethod
    def _optional_web_int(
        tool_name: str,
        arguments: dict[str, Any],
        field: str,
    ) -> tuple[int | None, dict[str, Any] | None]:
        value = arguments.get(field)
        if value is None:
            return None, None
        if isinstance(value, bool) or not isinstance(value, int):
            return None, BuiltinToolHandler._web_argument_error(
                tool_name,
                f"'{field}' must be an integer",
            )
        return value, None

    async def _run_web_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a bounded synchronous web operation outside the event loop."""
        if tool_name == WEB_SEARCH_TOOL.name:
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                return self._web_argument_error(tool_name, "requires a non-empty 'query' string")
            top_n, error = self._optional_web_int(tool_name, arguments, "top_n")
            if error is not None:
                return error
            fetch_results = arguments.get("fetch_results", True)
            if not isinstance(fetch_results, bool):
                return self._web_argument_error(tool_name, "'fetch_results' must be a boolean")
            result = await asyncio.to_thread(
                self._web_toolkit.search_gather,
                query,
                top_n=5 if top_n is None else top_n,
                fetch_results=fetch_results,
            )
            return result.model_dump(mode="json")

        url = arguments.get("url")
        if tool_name == WEB_CRAWL_TOOL.name:
            url = arguments.get("seed_url")
        if not isinstance(url, str) or not url.strip():
            required = "seed_url" if tool_name == WEB_CRAWL_TOOL.name else "url"
            return self._web_argument_error(
                tool_name,
                f"requires a non-empty '{required}' string",
            )
        url = url.strip()
        if tool_name == WEB_FETCH_TOOL.name:
            method = arguments.get("method", "GET")
            result = await asyncio.to_thread(self._web_toolkit.fetch_raw, url, method=method)
        elif tool_name == WEB_FETCH_PARSED_TOOL.name:
            result = await asyncio.to_thread(self._web_toolkit.fetch_parsed, url)
        elif tool_name == WEB_CRAWL_TOOL.name:
            max_pages, error = self._optional_web_int(tool_name, arguments, "max_pages")
            if error is not None:
                return error
            max_depth, error = self._optional_web_int(tool_name, arguments, "max_depth")
            if error is not None:
                return error
            result = await asyncio.to_thread(
                self._web_toolkit.crawl_site,
                url,
                max_pages=max_pages,
                max_depth=max_depth,
            )
        elif tool_name == WEB_RENDER_TOOL.name:
            result = await asyncio.to_thread(self._web_toolkit.render_js, url)
        else:
            return self._web_argument_error(tool_name, "unknown web operation", url=url)
        return result.model_dump(mode="json")


def register_builtins(
    client: Any,
    default_workspace: str | Path | None = None,
    *,
    web_retriever: WebRetriever | None = None,
    web_toolkit: WebToolkit | None = None,
) -> None:
    """Register the ``gludd-builtin`` server + its tools on ``client``.

    Safe to call once per :class:`~general_ludd.mcp.client.MCPClient`; does not
    touch any external MCP server flow. ``default_workspace`` (typically the
    engine's workspace_path) is used when a ``run_project_check`` call omits an
    explicit ``workspace`` argument.

    ``web_retriever``, when provided, is reused for all ``web_retrieve`` calls
    (shared cache). When omitted the handler constructs a fresh
    :class:`WebRetriever` per call. ``web_toolkit`` supplies the additive,
    DNS-pinned fetch/parse/search/crawl/render implementation; its optional
    search and offline-render providers remain unconfigured by default.
    """
    handler = BuiltinToolHandler(
        default_workspace=default_workspace,
        web_retriever=web_retriever,
        web_toolkit=web_toolkit,
    )
    client.register_builtin(
        BUILTIN_SERVER_ID,
        [RUN_PROJECT_CHECK_TOOL, WEB_RETRIEVE_TOOL, *_WEB_TOOLS],
        handler,
    )
    logger.debug(
        "registered builtin MCP server %r with tools: %s",
        BUILTIN_SERVER_ID,
        [RUN_PROJECT_CHECK_TOOL.name, WEB_RETRIEVE_TOOL.name, *(tool.name for tool in _WEB_TOOLS)],
    )
