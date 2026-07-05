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


class BuiltinToolHandler:
    """Coroutine dispatcher backing the ``gludd-builtin`` synthetic server."""

    def __init__(
        self,
        default_workspace: str | Path | None = None,
        *,
        web_retriever: WebRetriever | None = None,
    ) -> None:
        self._default_workspace = default_workspace
        self._web_retriever = web_retriever

    async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == RUN_PROJECT_CHECK_TOOL.name:
            return await self._run_project_check(arguments)
        if tool_name == WEB_RETRIEVE_TOOL.name:
            return await self._web_retrieve(arguments)
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


def register_builtins(
    client: Any,
    default_workspace: str | Path | None = None,
    *,
    web_retriever: WebRetriever | None = None,
) -> None:
    """Register the ``gludd-builtin`` server + its tools on ``client``.

    Safe to call once per :class:`~general_ludd.mcp.client.MCPClient`; does not
    touch any external MCP server flow. ``default_workspace`` (typically the
    engine's workspace_path) is used when a ``run_project_check`` call omits an
    explicit ``workspace`` argument.

    ``web_retriever``, when provided, is reused for all ``web_retrieve`` calls
    (shared cache).  When omitted the handler constructs a fresh
    :class:`WebRetriever` per call.
    """
    handler = BuiltinToolHandler(
        default_workspace=default_workspace,
        web_retriever=web_retriever,
    )
    client.register_builtin(
        BUILTIN_SERVER_ID,
        [RUN_PROJECT_CHECK_TOOL, WEB_RETRIEVE_TOOL],
        handler,
    )
    logger.debug(
        "registered builtin MCP server %r with tools: %s",
        BUILTIN_SERVER_ID,
        [RUN_PROJECT_CHECK_TOOL.name, WEB_RETRIEVE_TOOL.name],
    )
