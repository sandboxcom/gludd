"""MCP tool integration for model gateway calls.

Extends ExecutionEngine to handle tool-call loops where the model
requests tools via function calling, the engine executes them via MCP,
and the results are fed back to continue the conversation.
"""

from __future__ import annotations

import logging
from typing import Any

from general_ludd.schemas.job import JobSpec

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
PER_TOOL_TIMEOUT_SECONDS = 30


class ToolCallLoop:
    def __init__(
        self,
        model_gateway: Any,
        mcp_client: Any = None,
        max_iterations: int = MAX_TOOL_ITERATIONS,
        per_tool_timeout: float = PER_TOOL_TIMEOUT_SECONDS,
    ) -> None:
        self._gateway = model_gateway
        self._mcp_client = mcp_client
        self._max_iterations = max_iterations
        self._per_tool_timeout = per_tool_timeout

    def is_available(self) -> bool:
        return self._mcp_client is not None

    async def run_with_tools(
        self,
        job: JobSpec,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if self._mcp_client is None:
            return await self._call_model(job, system_prompt, user_prompt)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        tools = await self._mcp_client.list_tools()
        tool_schemas = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

        for _ in range(self._max_iterations):
            response = await self._call_with_tools(job, messages, tool_schemas)
            content = getattr(response, "content", "") or str(response)
            tool_calls = getattr(response, "tool_calls", None)

            if tool_calls:
                for tc in tool_calls:
                    tc_name = tc.get("function", {}).get("name", "")
                    tc_args = tc.get("function", {}).get("arguments", "{}")
                    if isinstance(tc_args, str):
                        import json as _json
                        try:
                            tc_args = _json.loads(tc_args)
                        except _json.JSONDecodeError:
                            tc_args = {}
                    try:
                        result = await self._mcp_client.call_tool(
                            None, tc_name, tc_args,
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": str(result),
                        })
                    except Exception as exc:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": f"Tool error: {exc}",
                        })
                continue
            return content

        logger.warning(
            "Tool call loop reached max iterations (%d) for job %s",
            self._max_iterations, job.job_id,
        )
        return content

    async def _call_model(
        self, job: JobSpec, system_prompt: str, user_prompt: str,
    ) -> Any:
        return self._gateway.call_model(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    async def _call_with_tools(
        self, job: JobSpec, messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> Any:
        return self._gateway.call_model(
            messages=messages,
            tools=tool_schemas,
        )
