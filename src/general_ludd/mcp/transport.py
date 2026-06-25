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
import re
import shutil
from typing import Any

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool

# Package managers / runtimes that are explicitly permitted as MCP launchers.
# Anything not on this list is rejected by default (operator can opt out via
# GLUDD_MCP_ALLOW_ANY_EXEC=1).
_MCP_EXEC_ALLOWLIST = frozenset(
    {"npx", "npm", "pnpm", "yarn", "bunx", "uvx", "python", "python3", "node"}
)

# Package managers that fetch-and-run code from a remote index.
# D8: bunx added here so _REMOTE_FETCH_LAUNCHERS includes it and the pin gate fires.
_NPM_FAMILY_LAUNCHERS = frozenset({"npx", "npm", "pnpm", "yarn", "bunx"})
# pip-style runners that also download from a remote index.
_UVX_FAMILY_LAUNCHERS = frozenset({"uvx"})
# All launchers that need package-spec injection validation.
_REMOTE_FETCH_LAUNCHERS = _NPM_FAMILY_LAUNCHERS | _UVX_FAMILY_LAUNCHERS

# Shell metacharacters that must never appear in a package spec or binary name
# passed to a remote-fetch launcher. These would be harmless in exec()-land
# (no shell expansion), but their presence strongly suggests an injection
# attempt — refuse early rather than relying on the exec layer to be safe.
_SHELL_META_RE = re.compile(r"[;&|$`\\<>()\s]")


def _strip_suffix(name: str) -> str:
    """Remove a Windows-style .cmd/.exe/.bat/.ps1 suffix from an executable name."""
    return re.sub(r"\.(cmd|exe|bat|ps1)$", "", name)


# Pattern for a concrete npm version pin: ``pkg@<number>`` or ``pkg@<n.n.n>``
# (including pre-release and build metadata). Rejects ``@latest``, ``@^1.x``,
# ``@~1.0`` and any other range or tag specifiers so only exact versions pass.
_VERSION_PINNED_RE = re.compile(
    r"@"                      # version separator
    r"(?!latest$|next$)"      # NOT the special "latest"/"next" dist-tags
    r"\d"                     # must start with a digit (not ^, ~, >, <, =, *, etc.)
    r"[0-9a-zA-Z.\-+]*$"      # allow semver-compat chars (pre-release + build meta)
)


def _is_version_pinned_spec(spec: str) -> bool:
    """Return True iff ``spec`` is a concretely version-pinned npm package spec.

    Accepts:
    * ``pkg@1.2.3`` — simple semver pin
    * ``@scope/pkg@2026.1.26`` — scoped package with date-based version
    * ``pkg@2`` — major-only pin (still concrete, not a range)

    Rejects:
    * ``pkg`` — bare name (no version)
    * ``@scope/pkg`` — scoped, no version
    * ``pkg@latest`` / ``pkg@next`` — dist-tags (float, not pinned)
    * ``pkg@^1.0.0`` / ``pkg@~1.0.0`` / ``pkg@>=1.0`` — semver ranges
    """
    # The last '@' (after stripping a leading '@' for scoped packages) is the
    # version separator for npm. For scoped packages like ``@scope/pkg@ver`` the
    # last '@' is the right one; for plain ``pkg@ver`` it's the only one.
    if not spec:
        return False
    # Strip a leading '@' that's part of the scope, then look for the LAST '@'.
    remainder = spec.lstrip("@")
    at_pos = remainder.rfind("@")
    if at_pos < 0:
        # No version separator at all.
        return False
    version_part = remainder[at_pos:]   # includes the leading '@'
    return bool(_VERSION_PINNED_RE.match(version_part))


def _launcher_basename(cmd0: str) -> str:
    """Normalised (lowercase, suffix-stripped) basename of argv[0]."""
    return _strip_suffix(os.path.basename(cmd0).lower())


def _validate_launch_command(cmd: list[str]) -> None:
    """Validate ``cmd`` before spawning an MCP subprocess.  Fail closed on any
    policy violation.

    Checks (in order):
    1. Empty argv → MCPTransportError("empty …")
    2. Executable basename must be on the allowlist (or GLUDD_MCP_ALLOW_ANY_EXEC=1).
    3. Executable must resolve via ``shutil.which`` or be an existing absolute path.
    4. For remote-fetch launchers (npx/npm/pnpm/yarn/uvx): every non-flag token
       that acts as a package spec must not contain shell metacharacters.  An arg
       that consists ENTIRELY of ``-…`` tokens (no package spec at all) is also
       rejected, since a bare ``npx --some-flag`` with no package name is
       semantically broken and likely injection.
    """
    if not cmd:
        raise MCPTransportError(
            "Refusing to spawn MCP subprocess: empty command (argv is empty)."
        )

    launcher = _launcher_basename(cmd[0])
    allow_any = os.environ.get("GLUDD_MCP_ALLOW_ANY_EXEC", "").strip() in {
        "1", "true", "yes",
    }

    if not allow_any and launcher not in _MCP_EXEC_ALLOWLIST:
        raise MCPTransportError(
            f"Executable {cmd[0]!r} (basename {launcher!r}) is not in the MCP "
            f"executable allowlist {sorted(_MCP_EXEC_ALLOWLIST)}. Set "
            "GLUDD_MCP_ALLOW_ANY_EXEC=1 to opt out of this check."
        )

    # Resolve the executable — accept absolute paths that exist on disk.
    resolved = shutil.which(cmd[0])
    if resolved is None and not os.path.isfile(cmd[0]):
        raise MCPTransportError(
            f"MCP executable {cmd[0]!r} could not be resolved on PATH and is "
            "not an existing absolute path.  Check the MCP server config."
        )

    # Package-spec injection guard for remote-fetch launchers.
    if launcher in _REMOTE_FETCH_LAUNCHERS:
        _validate_package_spec(cmd, launcher)


# JS npm-family launchers whose package spec MUST be version-pinned (a mutable
# dist-tag / range / bare name is a supply-chain substitution risk). uvx
# (Python) is intentionally excluded — its pinning semantics differ.
# D8: _NPM_FAMILY_LAUNCHERS is defined once at module top (includes bunx).


def _validate_package_spec(cmd: list[str], launcher: str) -> None:
    """Validate the package spec argument(s) for a remote-fetch launcher.

    Raises MCPTransportError if:
    - The first non-flag argument (the package spec) contains shell metacharacters.
    - There are ONLY flag arguments (no package spec at all — likely injection).
    - A value passed via --package / -p (flags that supply a package spec to
      npx/npm/pnpm/yarn/bunx) is not version-pinned or contains metacharacters.
      These flags bypass the original positional-arg check and represent a
      supply-chain substitution risk identical to an unpinned positional spec.
    """
    # Flags that accept a following argument which is itself a package spec.
    # npx supports both ``--package pkg`` and ``-p pkg``; pnpm/yarn follow the
    # same convention.  Values supplied this way must pass the same checks as
    # the positional package spec.
    _PACKAGE_VALUE_FLAGS = frozenset({"--package", "-p"})

    args_after_launcher = cmd[1:]
    found_spec = False

    def _check_spec(arg: str) -> None:
        """Apply metacharacter and version-pin checks to a single spec."""
        if _SHELL_META_RE.search(arg):
            raise MCPTransportError(
                f"MCP package spec {arg!r} for launcher {launcher!r} contains "
                "shell metacharacters. This looks like an injection attempt and "
                "is refused."
            )
        if launcher in _NPM_FAMILY_LAUNCHERS and not _is_version_pinned_spec(arg):
            raise MCPTransportError(
                f"MCP package spec {arg!r} for launcher {launcher!r} is not "
                "version-pinned (bare name, dist-tag, or range). Pin it to a "
                "concrete version (e.g. pkg@1.2.3) — refused for supply-chain safety."
            )

    i = 0
    while i < len(args_after_launcher):
        arg = args_after_launcher[i]
        if arg in _PACKAGE_VALUE_FLAGS:
            # The next token is the package spec supplied via this flag.
            if i + 1 >= len(args_after_launcher):
                raise MCPTransportError(
                    f"MCP launcher {launcher!r}: flag {arg!r} requires a "
                    "following package-spec argument but none was found."
                )
            spec = args_after_launcher[i + 1]
            _check_spec(spec)
            found_spec = True
            i += 2
            continue
        if arg.startswith("--package=") or arg.startswith("-p="):
            # ``--package=pkg@1.2.3`` inline form.
            spec = arg.split("=", 1)[1]
            _check_spec(spec)
            found_spec = True
            i += 1
            continue
        if not arg.startswith("-"):
            # First positional non-flag arg is the package spec.
            _check_spec(arg)
            found_spec = True
            # Remaining args are arguments forwarded to the server binary, not
            # package specs — stop scanning here.
            break
        i += 1

    if not found_spec:
        # No non-flag arg found — no package spec supplied.
        raise MCPTransportError(
            f"MCP launcher {launcher!r} has no package spec argument (only flags "
            "were found). Provide a package name to fetch/run."
        )

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


class MCPTransportError(Exception):
    pass


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

        An oversized frame (>64 KB asyncio default) raises ValueError or
        LimitOverrunError from readline().  Catch those too: force-terminate
        the subprocess so it doesn't linger, then raise a clean
        MCPTransportError rather than leaking the raw exception.
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
        except (ValueError, asyncio.LimitOverrunError) as exc:
            # asyncio raises LimitOverrunError (subclass of Exception) or
            # ValueError when a line exceeds the StreamReader buffer limit
            # (~64 KB by default).  The subprocess is left running if we do
            # not kill it, so terminate first, then surface a clean error.
            await self._force_terminate()
            raise MCPTransportError(
                "MCP server sent an oversized JSON-RPC frame (exceeds "
                "asyncio StreamReader limit).  The subprocess has been "
                "terminated."
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
        # LAUNCH VALIDATION: fail closed BEFORE spawning — check allowlist,
        # PATH resolution, and package-spec injection in one go.
        _validate_launch_command(cmd)
        env = self._build_env()

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # PIPE would block on >64KB stderr writes
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
