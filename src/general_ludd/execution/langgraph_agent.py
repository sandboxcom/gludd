"""LangGraph-based agent loop replacing the hand-rolled ToolCallLoop.

Wraps ``langgraph.prebuilt.create_react_agent`` + ``ToolNode`` so the daemon's
event loop can dispatch autonomous tool-using agents through LangGraph's native
agent runtime instead of the custom while-loop in ``tool_loop.py``.

Follow-up items (not yet implemented):
  - SLM context compaction between iterations (langgraph middleware/checkpointer hook)
  - Tool auditor + situation store integration (wrap ToolNode.invoke or use a
    custom pre-node hook)
  - Per-tool timeout (langgraph ToolNode calls tools synchronously; timeout
    would need a custom async tool wrapper or a graph interrupt)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError
from general_ludd.security.capability_lattice import check_dispatch

if TYPE_CHECKING:
    from general_ludd.schemas.job import JobSpec

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
PER_TOOL_TIMEOUT_SECONDS = 30


class LangGraphAgentLoop:
    """Agent loop backed by langgraph's ``create_react_agent`` + ``ToolNode``.

    Takes a LangChain chat model (from ``ModelGateway.get_chat_model``), an MCP
    client for tool execution, and configuration.  Calls ``graph.ainvoke()`` with
    the conversation messages and returns the final assistant content.

    Capability gating and server_id resolution are preserved from the original
    ``ToolCallLoop`` — they run inside each tool wrapper function before the MCP
    call is dispatched.
    """

    def __init__(
        self,
        model_gateway: Any,
        chat_model: Any = None,
        mcp_client: Any = None,
        max_iterations: int = MAX_TOOL_ITERATIONS,
        per_tool_timeout: float = PER_TOOL_TIMEOUT_SECONDS,
        mcp_registry: MCPToolRegistry | None = None,
        role: str | None = None,
        tool_auditor: Any = None,
    ) -> None:
        self._gateway = model_gateway
        self._chat_model = chat_model
        self._mcp_client = mcp_client
        self._max_iterations = max_iterations
        self._per_tool_timeout = per_tool_timeout
        self._auditor = tool_auditor
        self._role = role
        self._mcp_registry = mcp_registry
        if mcp_registry is None and mcp_client is not None:
            self._mcp_registry = getattr(mcp_client, "_registry", None)

    def _resolve_server_id(self, tc_name: str) -> str:
        registry = self._mcp_registry
        if registry is None:
            raise MCPTransportError(
                "MCP tool registry unavailable; refusing ungated tool call "
                f"{tc_name!r}"
            )
        tool = registry.get_tool(tc_name)
        if tool is None:
            if tc_name in registry.tool_names():
                raise MCPTransportError(
                    f"Tool {tc_name!r} is ambiguous across multiple servers; "
                    f"supply an explicit server_id"
                )
            raise MCPTransportError(
                f"Tool {tc_name!r} is not a registered MCP tool (capability "
                f"gate); refusing call"
            )
        if not tool.server_id:
            raise MCPTransportError(
                f"Tool {tc_name!r} is not a registered MCP tool (capability "
                f"gate); refusing call"
            )
        return tool.server_id

    def is_available(self) -> bool:
        return self._mcp_client is not None

    async def run_with_tools(
        self,
        job: JobSpec,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if self._mcp_client is None:
            return await self._run_plain(job, system_prompt, user_prompt)

        if self._role is not None:
            check_dispatch(self._role, "mcp")

        langchain_tools = await self._build_langchain_tools()
        model = await self._resolve_chat_model(langchain_tools)

        try:
            from langgraph.prebuilt import create_react_agent
        except ImportError as exc:
            raise ImportError(
                "langgraph is required for LangGraphAgentLoop. "
                "Install with: pip install langgraph"
            ) from exc

        graph: Any = create_react_agent(
            model,
            langchain_tools,
        )

        from langchain_core.messages import HumanMessage, SystemMessage

        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=user_prompt))

        config: dict[str, Any] = {
            "recursion_limit": self._max_iterations * 2 + 10,
        }

        try:
            result = await graph.ainvoke(
                {"messages": messages},
                config=config,
            )
        except Exception as exc:
            logger.warning(
                "LangGraph agent loop failed for job %s: %s",
                job.job_id,
                exc,
                exc_info=True,
            )
            raise

        output_messages = result.get("messages", [])
        for msg in reversed(output_messages):
            if hasattr(msg, "content") and msg.content and getattr(msg, "type", "") == "ai":
                return str(msg.content)

        logger.warning(
            "LangGraph agent loop: no AI message with content found for job %s",
            job.job_id,
        )
        return ""

    async def _run_plain(
        self,
        job: JobSpec,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        profile_id = job.model_profile or "default"
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        response = await asyncio.to_thread(
            self._gateway.call_model,
            profile_id,
            messages=messages,
            work_type=job.work_type,
            project_id=job.project_id,
        )
        return str(getattr(response, "content", "") or str(response))

    async def _resolve_chat_model(
        self,
        langchain_tools: list[Any],
    ) -> Any:
        """Get a LangChain chat model with tools bound, suitable for create_react_agent."""
        if self._chat_model is not None and langchain_tools and hasattr(
            self._chat_model, "bind_tools"
        ):
            return self._chat_model.bind_tools(langchain_tools)
        return self._chat_model

    async def _build_langchain_tools(self) -> list[Any]:
        """Convert MCP tools to LangChain tools with capability gates + timeouts."""
        if self._mcp_client is None:
            return []

        from langchain_core.tools import StructuredTool

        mcp_tools = await self._mcp_client.list_tools()
        langchain_tools: list[Any] = []

        for mcp_tool in mcp_tools:
            tool_name = mcp_tool.name
            tool_desc = mcp_tool.description or f"MCP tool: {tool_name}"
            server_id = self._resolve_server_id(tool_name)

            mcp_client = self._mcp_client
            timeout = self._per_tool_timeout

            async def _execute(
                _name: str = tool_name,
                _srv_id: str = server_id,
                _client: Any = mcp_client,
                _tmo: float = timeout,
                **kwargs: Any,
            ) -> str:
                try:
                    result = await asyncio.wait_for(
                        _client.call_tool(_srv_id, _name, kwargs),
                        timeout=_tmo,
                    )
                    return str(result)
                except TimeoutError:
                    return f"Tool error: {_name!r} timed out after {_tmo}s"
                except Exception as exc:
                    return f"Tool error: {exc}"

            lc_tool = StructuredTool.from_function(
                coroutine=_execute,
                name=tool_name,
                description=tool_desc,
            )
            langchain_tools.append(lc_tool)

        return langchain_tools
