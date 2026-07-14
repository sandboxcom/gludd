"""Stdlib-only MCP server wrapping the ornith binary via subprocess."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys
from collections import OrderedDict
from typing import Any

from general_ludd.ornith.sandbox import ornith_sandbox_preexec

_NOT_INSTALLED_ERROR = "ornith not installed (ORNITH_ENABLED not set)"


class OrnithMCPServer:
    def __init__(
        self,
        ornith_binary_path: str = "ornith",
        ornith_model_sha: str = "",
        timeout_seconds: int = 300,
        enabled: bool = False,
    ) -> None:
        self._binary_path = ornith_binary_path
        self._model_sha = ornith_model_sha
        self._timeout_seconds = timeout_seconds
        self._enabled = enabled

        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cache_maxsize = 128

        self._total_calls = 0
        self._successful_calls = 0
        self._last_call_at: str | None = None
        self._version: str | None = None

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "ornith_solve",
                "description": (
                    "Submit a coding task to the ornith self-improving coding LLM "
                    "and receive a patch + summary."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["task_description", "repo_context_path"],
                    "properties": {
                        "task_description": {"type": "string"},
                        "repo_context_path": {"type": "string"},
                        "max_iterations": {"type": "integer", "default": 10},
                        "target_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": [],
                        },
                    },
                },
            },
            {
                "name": "ornith_improve",
                "description": (
                    "Improve an existing artifact (playbook/module/plugin/rego) "
                    "based on structured feedback."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": [
                        "target_artifact_path",
                        "feedback_yaml",
                        "artifact_kind",
                    ],
                    "properties": {
                        "target_artifact_path": {"type": "string"},
                        "feedback_yaml": {"type": "string"},
                        "artifact_kind": {
                            "type": "string",
                            "enum": ["playbook", "module", "plugin", "rego"],
                        },
                        "max_iterations": {"type": "integer", "default": 10},
                    },
                },
            },
        ]

    def list_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "ornith_status",
                "description": "Runtime status of the ornith integration.",
                "mimeType": "application/json",
            },
            {
                "name": "ornith_model_info",
                "description": "Metadata about the underlying ornith model.",
                "mimeType": "application/json",
            },
        ]

    def list_prompts(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "ornith_meta",
                "description": (
                    "Meta-prompt describing how to drive ornith for "
                    "self-improvement loops."
                ),
                "arguments": [],
            }
        ]

    def _cache_key(self, name: str, arguments: dict[str, Any]) -> str:
        payload = json.dumps(arguments, sort_keys=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{name}:{digest}"

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def _cache_put(self, key: str, value: dict[str, Any]) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_maxsize:
            self._cache.popitem(last=False)

    def _not_installed_result(self) -> dict[str, Any]:
        return {
            "installed": False,
            "error": _NOT_INSTALLED_ERROR,
            "patch": None,
            "summary": None,
        }

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        key = self._cache_key(name, arguments)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        if not self._enabled:
            result = self._not_installed_result()
            self._cache_put(key, result)
            return result

        self._total_calls += 1
        self._last_call_at = datetime.datetime.now(datetime.UTC).isoformat()

        proc = subprocess.run(
            [self._binary_path, "--json", json.dumps(arguments)],
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            preexec_fn=ornith_sandbox_preexec,
        )

        parsed: dict[str, Any]
        try:
            parsed = json.loads(proc.stdout) if proc.stdout else {}
            if not isinstance(parsed, dict):
                parsed = {}
        except (ValueError, TypeError):
            parsed = {}

        if proc.returncode == 0:
            self._successful_calls += 1
            if isinstance(parsed.get("version"), str):
                self._version = parsed["version"]

        parsed["installed"] = True
        self._cache_put(key, parsed)
        return parsed

    def handle_resource_read(self, name: str) -> dict[str, Any]:
        if name == "ornith_status":
            success_rate = (
                self._successful_calls / self._total_calls
                if self._total_calls > 0
                else 0.0
            )
            return {
                "installed": self._enabled,
                "version": self._version,
                "last_call_at": self._last_call_at,
                "total_calls": self._total_calls,
                "success_rate": success_rate,
            }
        if name == "ornith_model_info":
            return {
                "model_sha": self._model_sha,
                "size_bytes": 0,
                "capabilities": [
                    "code_gen",
                    "scaffold_improvement",
                    "tool_calling",
                ],
            }
        return {}


def _jsonrpc_response(req_id: Any, result: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_error(req_id: Any, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


def _handle_request(server: OrnithMCPServer, request: dict[str, Any]) -> str:
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        return _jsonrpc_response(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "ornith-mcp", "version": "0.1.0"},
            },
        )
    if method == "tools/list":
        return _jsonrpc_response(req_id, {"tools": server.list_tools()})
    if method == "resources/list":
        return _jsonrpc_response(req_id, {"resources": server.list_resources()})
    if method == "prompts/list":
        return _jsonrpc_response(req_id, {"prompts": server.list_prompts()})
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        return _jsonrpc_response(
            req_id, {"content": [{"type": "text", "text": json.dumps(server.handle_tool_call(name, arguments))}]}
        )
    if method == "resources/read":
        uri = params.get("uri", "")
        name = uri.split("://", 1)[-1] if "://" in uri else uri
        text = server.handle_resource_read(name)
        return _jsonrpc_response(
            req_id,
            {"contents": [{"type": "resource", "uri": uri, "text": json.dumps(text)}]},
        )
    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    _server = OrnithMCPServer(enabled=os.environ.get("ORNITH_ENABLED", "").lower() in {"1", "true", "yes"})
    for _line in sys.stdin:
        _line = _line.strip()
        if not _line:
            continue
        try:
            _req = json.loads(_line)
        except ValueError:
            sys.stderr.write(f"ornith-mcp-server: invalid JSON: {_line}\n")
            continue
        if not isinstance(_req, dict):
            continue
        sys.stdout.write(_handle_request(_server, _req) + "\n")
        sys.stdout.flush()
