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
from pathlib import Path
from typing import Any

from general_ludd.mcp.registry import MCPTool
from general_ludd.project_runner import (
    ProjectCommandRunner,
    ProjectProfileError,
    load_project_profile,
)

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


class BuiltinToolHandler:
    """Coroutine dispatcher backing the ``gludd-builtin`` synthetic server."""

    def __init__(self, default_workspace: str | Path | None = None) -> None:
        self._default_workspace = default_workspace

    async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == RUN_PROJECT_CHECK_TOOL.name:
            return await self._run_project_check(arguments)
        return {"error": f"unknown builtin tool: {tool_name!r}"}

    async def _run_project_check(self, arguments: dict[str, Any]) -> dict[str, Any]:
        check_name = arguments.get("check_name")
        if not isinstance(check_name, str) or not check_name.strip():
            return {"error": "run_project_check requires a non-empty 'check_name' string"}
        check_name = check_name.strip()

        workspace_arg = arguments.get("workspace")
        workspace: str | Path
        if isinstance(workspace_arg, str) and workspace_arg.strip():
            workspace = workspace_arg.strip()
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


def register_builtins(client: Any, default_workspace: str | Path | None = None) -> None:
    """Register the ``gludd-builtin`` server + its tools on ``client``.

    Safe to call once per :class:`~general_ludd.mcp.client.MCPClient`; does not
    touch any external MCP server flow. ``default_workspace`` (typically the
    engine's workspace_path) is used when a ``run_project_check`` call omits an
    explicit ``workspace`` argument.
    """
    handler = BuiltinToolHandler(default_workspace=default_workspace)
    client.register_builtin(
        BUILTIN_SERVER_ID,
        [RUN_PROJECT_CHECK_TOOL],
        handler,
    )
    logger.debug(
        "registered builtin MCP server %r with tools: %s",
        BUILTIN_SERVER_ID,
        [RUN_PROJECT_CHECK_TOOL.name],
    )
