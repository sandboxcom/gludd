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
