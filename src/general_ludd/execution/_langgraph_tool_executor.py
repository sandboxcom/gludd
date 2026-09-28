"""Policy-aware executor factory for LangGraph-wrapped MCP tools."""

from __future__ import annotations

from typing import Any


def make_mcp_executor(
    *,
    tool_name: str,
    server_id: str,
    client: Any,
    timeout: float,
    auditor: Any,
    sandbox_enforcer: Any,
    sandbox_error: type[Exception],
    wait_for: Any,
) -> Any:
    """Bind one MCP tool's policy controls into its async executor."""

    async def _execute(
        *args: Any,
        _tool_name: str = tool_name,
        _server_id: str = server_id,
        _client: Any = client,
        _tmo: float = timeout,
        _aud: Any = auditor,
        _sandbox: Any = sandbox_enforcer,
        **kwargs: Any,
    ) -> str:
        if args and isinstance(args[0], dict):
            input_data = args[0]
        elif kwargs:
            input_data = kwargs
        else:
            input_data = {}
        if _sandbox is not None:
            try:
                _sandbox.verify_ready()
            except sandbox_error as exc:
                return (
                    "Tool error: sandbox not available — "
                    f"refusing to execute {_tool_name!r}: {exc}"
                )
            for _key, _val in input_data.items():
                if isinstance(_val, str) and _key in (
                    "path", "file", "file_path", "workdir", "output",
                    "dir", "directory", "cwd", "out_path",
                ):
                    try:
                        _sandbox.confine_path(_val)
                    except Exception as exc:
                        return (
                            f"Tool error: path {_val!r} escapes sandbox "
                            f"for tool {_tool_name!r}: {exc}"
                        )
        if _aud is not None:
            verdict = _aud.audit(
                _tool_name, input_data,
                task_context="langgraph_agent",
            )
            if verdict is not None and not verdict.allowed:
                return (
                    "Tool error: tool call blocked by auditor: "
                    f"{verdict.classification}. "
                    f"{verdict.reason} "
                    "Do not retry this call. Use a different approach."
                )
        try:
            result = await wait_for(
                _client.call_tool(_server_id, _tool_name, input_data),
                timeout=_tmo,
            )
            if _aud is not None:
                _aud.record_success(_tool_name, input_data, result)
            return str(result)
        except TimeoutError:
            if _aud is not None:
                _aud.record_error(
                    _tool_name, input_data,
                    f"timeout after {_tmo}s",
                )
            return f"Tool error: {_tool_name!r} timed out after {_tmo}s"
        except Exception as exc:
            if _aud is not None:
                _aud.record_error(_tool_name, input_data, str(exc))
            return f"Tool error: {exc}"

    return _execute
