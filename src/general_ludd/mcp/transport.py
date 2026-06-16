"""MCP stdio client transport — hand-rolled, kept as-is.

KEEP LIST (V3.2): Both named protocol bugs are fixed in this file:
  - transport.py:52 matches responses by ``id`` to guard against interleaving.
  - transport.py:98 sends ``notifications/initialized`` after handshake.
The official ``mcp`` Python SDK is NOT a declared dependency; adopting it would
add a heavy transitive closure for marginal benefit. The two-bug rationale from
guide 2 no longer applies (both fixed). Decision: keep this 125-LOC client
until a concrete need for SDK features (e.g., sampling, roots, auth) arises.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from typing import Any

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool

# Cap on how many non-matching (interleaved) JSON-RPC frames we will skip while
# waiting for our request's response before giving up. Bounds the read loop so a
# misbehaving server that streams unrelated frames can never spin it forever
# (defense in depth alongside the per-read timeout). Finding 5.
_MAX_INTERLEAVE_SKIPS = 100

# Minimal base environment handed to every MCP subprocess. The full host
# environment (which includes ANTHROPIC_API_KEY, GLUDD_PSK, cloud creds, etc.)
# is NEVER inherited — only these process-hygiene vars plus the server's own
# declared `env`/resolved secrets are passed. Finding 2.
_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")

# Known MCP server runtimes. The executable that gets exec'd must have one of
# these basenames unless the operator explicitly opts out via
# GLUDD_MCP_ALLOW_ANY_EXEC=1. This is a launch-time guard against a poisoned /
# typo'd config exec'ing an arbitrary binary (e.g. /bin/sh) on the host.
# Finding #65.
_EXEC_ALLOWLIST = frozenset(
    {
        "npx",
        "uvx",
        "uv",
        "python",
        "python3",
        "node",
        "deno",
        "bunx",
        "bun",
    }
)

# Env flag operators set to run a non-allowlisted MCP runtime on purpose.
_ALLOW_ANY_EXEC_ENV = "GLUDD_MCP_ALLOW_ANY_EXEC"

# Package-fetching runtimes whose first non-flag argument is a package spec
# (npx <pkg>, uvx <pkg>, uv tool run <pkg>). That spec is validated to block
# leading-dash flag injection and shell metacharacters even though the command
# is never shell-interpreted — a metacharacter-laden spec is a config smell and
# some runtimes re-shell their args internally. Finding #65.
_PACKAGE_RUNTIMES = frozenset({"npx", "uvx", "bunx"})

# Characters that have no business in a package spec. Their presence indicates
# either an injection attempt or a malformed config; reject fail-closed.
_SHELL_METACHARACTERS = frozenset(';&|`$<>(){}[]!*?~\n\r\t \\"\'')


class MCPTransportError(Exception):
    pass


def _executable_basename(exe: str) -> str:
    """Basename of the executable, stripped of any directory and extension."""
    base = os.path.basename(exe)
    root, _ext = os.path.splitext(base)
    return root or base


def _validate_package_spec(runtime: str, spec: str) -> None:
    """Reject injection-y package specs handed to npx/uvx/bunx.

    The first non-flag argument to a package-fetching runtime is a package
    name/spec. A leading dash turns it into a flag (e.g. ``npx --foo``), and
    shell metacharacters indicate an injection attempt or malformed config.
    """
    if spec.startswith("-"):
        raise MCPTransportError(
            f"{runtime} package spec {spec!r} looks like a flag "
            f"(leading '-'); refusing to launch (flag-injection guard)"
        )
    bad = sorted(set(spec) & _SHELL_METACHARACTERS)
    if bad:
        raise MCPTransportError(
            f"{runtime} package spec {spec!r} contains disallowed "
            f"character(s) {bad!r}; refusing to launch"
        )


# Flags accepted before the package spec for package-fetching runtimes. We keep
# this deliberately tight (npx -y / --yes, uvx --from <X>, etc.); anything else
# is treated as an attempt to slip arbitrary flags past the package guard.
_PACKAGE_RUNTIME_SAFE_FLAGS = frozenset(
    {"-y", "--yes", "-q", "--quiet", "--from", "-p", "--python"}
)


def _validate_package_runtime_args(runtime: str, args: list[str]) -> None:
    """Locate and validate the package spec for a package-fetching runtime.

    Walks the leading flags (only a tight allowlist of known-safe flags is
    tolerated) and validates the first positional argument as the package spec.
    A package runtime invoked with no package spec at all is rejected.
    """
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg.startswith("-"):
            _validate_package_spec(runtime, arg)
            return
        # A leading-dash token here is a flag; only known-safe flags may appear
        # before the package spec.
        if arg not in _PACKAGE_RUNTIME_SAFE_FLAGS:
            raise MCPTransportError(
                f"{runtime} package spec {arg!r} looks like a flag "
                f"(leading '-'); refusing to launch (flag-injection guard)"
            )
        # `--from <X>` / `-p <X>` style flags consume the next token.
        if arg in {"--from", "-p", "--python"}:
            i += 2
        else:
            i += 1
    raise MCPTransportError(
        f"{runtime} invoked without a package spec; refusing to launch"
    )


def _validate_launch_command(argv: list[str]) -> None:
    """Validate the full argv before it is exec'd. Finding #65.

    - reject empty argv;
    - enforce the executable-basename allowlist (opt-out via env flag);
    - require the executable to resolve on PATH (or exist as an absolute path);
    - validate the package spec for package-fetching runtimes.

    The command is ALWAYS passed as an argv list to create_subprocess_exec and
    is never shell-interpreted; this function adds defense-in-depth on top.
    """
    if not argv or not argv[0]:
        raise MCPTransportError("MCP launch command is empty; refusing to launch")

    exe = argv[0]
    basename = _executable_basename(exe)

    allow_any = os.environ.get(_ALLOW_ANY_EXEC_ENV, "") == "1"
    if not allow_any and basename not in _EXEC_ALLOWLIST:
        allowed = ", ".join(sorted(_EXEC_ALLOWLIST))
        raise MCPTransportError(
            f"executable {exe!r} (basename {basename!r}) is not in the MCP "
            f"executable allowlist ({allowed}); set "
            f"{_ALLOW_ANY_EXEC_ENV}=1 to override"
        )

    # The executable must actually exist. shutil.which resolves bare names on
    # PATH; an absolute/relative path is accepted only if it exists.
    if os.path.sep in exe or (os.path.altsep and os.path.altsep in exe):
        resolved = exe if os.path.exists(exe) else None
    else:
        resolved = shutil.which(exe)
    if resolved is None:
        raise MCPTransportError(
            f"executable {exe!r} could not be resolved on PATH; "
            f"refusing to launch"
        )

    if basename in _PACKAGE_RUNTIMES:
        _validate_package_runtime_args(basename, argv[1:])


class MCPStdioClient:
    """Manages a single MCP server subprocess via stdio JSON-RPC."""

    def __init__(self, config: MCPServerConfig, secrets_mgr: Any = None) -> None:
        self._config = config
        self._secrets_mgr = secrets_mgr
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0

    def _build_env(self) -> dict[str, str]:
        """Minimal allowlisted base env + the server's declared/resolved env.

        Finding 2: never hand the full host environment to the subprocess.
        Finding 7: if a secrets manager is available, resolve env_aliases (Vault/
        OpenBao) so credentials are injected at start time rather than stored in
        plaintext config; otherwise fall back to the static declared env.
        """
        base: dict[str, str] = {}
        for key in _ENV_ALLOWLIST:
            val = os.environ.get(key)
            if val is not None:
                base[key] = val

        if self._secrets_mgr is not None:
            # Local import avoids a module-load cycle (secrets imports config).
            from general_ludd.mcp.secrets import resolve_mcp_env

            server_env = resolve_mcp_env(self._config, self._secrets_mgr)
        else:
            server_env = dict(self._config.env)

        base.update(server_env)
        return base

    async def _readline_with_timeout(self) -> bytes:
        """readline() bounded by the configured timeout. Finding 1.

        On timeout the subprocess is force-terminated (it is presumed hung) and
        an MCPTransportError is raised so the caller fails fast instead of
        blocking forever.
        """
        assert self._process is not None
        assert self._process.stdout is not None
        try:
            return await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._force_terminate()
            raise MCPTransportError(
                f"MCP server timed out after {self._config.timeout_seconds}s "
                f"waiting for response (method read)"
            ) from exc

    async def _force_terminate(self) -> None:
        """Best-effort kill of a hung/misbehaving subprocess."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                proc.wait(), timeout=self._config.timeout_seconds
            )

    async def _drain_with_timeout(self) -> None:
        """stdin.drain() bounded by the configured timeout. Finding 1."""
        assert self._process is not None
        assert self._process.stdin is not None
        try:
            await asyncio.wait_for(
                self._process.stdin.drain(),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._force_terminate()
            raise MCPTransportError(
                f"MCP server timed out after {self._config.timeout_seconds}s "
                f"draining stdin"
            ) from exc

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._process is None or self._process.returncode is not None:
            raise MCPTransportError("Process not running")
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        request_id = self._next_id()
        request: dict[str, Any] = {
            "jsonrpc": "2.0", "id": request_id, "method": method,
        }
        if params is not None:
            request["params"] = params

        line = json.dumps(request) + "\n"
        self._process.stdin.write(line.encode())
        await self._drain_with_timeout()

        skips = 0
        while True:
            response_line = await self._readline_with_timeout()
            if not response_line:
                raise MCPTransportError("Connection closed")
            response = json.loads(response_line.decode())
            if response.get("id") != request_id:
                # Finding 5: bound the interleave-skip loop so a server that
                # streams a flood of unrelated frames can't spin us forever.
                skips += 1
                if skips >= _MAX_INTERLEAVE_SKIPS:
                    await self._force_terminate()
                    raise MCPTransportError(
                        f"Exceeded {_MAX_INTERLEAVE_SKIPS} interleaved frames "
                        f"without a response for id {request_id}"
                    )
                continue
            if "error" in response:
                raise MCPTransportError(
                    f"JSON-RPC error: {response['error']}"
                )
            return dict[str, Any](response.get("result", {}))

    async def _send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        assert self._process.stdin is not None
        notification: dict[str, Any] = {
            "jsonrpc": "2.0", "method": method,
        }
        if params is not None:
            notification["params"] = params
        line = json.dumps(notification) + "\n"
        self._process.stdin.write(line.encode())
        await self._drain_with_timeout()

    async def start(self) -> None:
        cmd = (self._config.command or []) + self._config.args
        # Finding #65: validate the argv (never shell-interpreted) before exec:
        # non-empty, allowlisted executable that resolves on PATH, and a clean
        # package spec for npx/uvx-style runtimes.
        _validate_launch_command(cmd)
        env = self._build_env()

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "general-ludd-agent", "version": "0.1.0",
                },
            },
        )

        await self._send_notification("notifications/initialized", {})

    async def list_tools(self) -> list[MCPTool]:
        result = await self._send_request("tools/list")
        tools: list[MCPTool] = []
        for tool_data in result.get("tools", []):
            tools.append(
                MCPTool(
                    name=tool_data["name"],
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                )
            )
        return tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

    async def stop(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            # Finding 4: bound the wait() so a process that ignores SIGTERM
            # can't hang stop() forever — escalate to kill() on timeout.
            try:
                await asyncio.wait_for(
                    self._process.wait(),
                    timeout=self._config.timeout_seconds,
                )
            except TimeoutError:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._process.wait(),
                        timeout=self._config.timeout_seconds,
                    )
