"""LangGraph-based agent loop replacing the hand-rolled ToolCallLoop.

Wraps ``langgraph.prebuilt.create_react_agent`` + ``ToolNode`` so the daemon's
event loop can dispatch autonomous tool-using agents through LangGraph's native
agent runtime instead of the custom while-loop in ``tool_loop.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from general_ludd.budget_guard_check import budget_pre_check
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError
from general_ludd.sandbox.enforcer import SandboxEnforcer, SandboxNotAvailableError
from general_ludd.security.capability_lattice import check_dispatch

if TYPE_CHECKING:
    from general_ludd.schemas.job import JobSpec

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
PER_TOOL_TIMEOUT_SECONDS = 30
MAX_TOTAL_TOKENS_DEFAULT = 100_000


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
        budget_guard: Any = None,
        adversarial_detector: Any = None,
        max_total_tokens: int | None = None,
        sandbox_enforcer: SandboxEnforcer | None = None,
    ) -> None:
        self._gateway = model_gateway
        self._chat_model = chat_model
        self._mcp_client = mcp_client
        self._max_iterations = max_iterations
        self._per_tool_timeout = per_tool_timeout
        self._auditor = tool_auditor
        self._role = role
        self._mcp_registry = mcp_registry
        self._budget_guard = budget_guard
        self._adversarial_detector = adversarial_detector
        self._max_total_tokens = max_total_tokens or MAX_TOTAL_TOKENS_DEFAULT
        self._sandbox_enforcer = sandbox_enforcer
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

        if self._budget_guard is not None:
            denial = budget_pre_check(self._budget_guard)
            if denial is not None:
                logger.warning(
                    "LangGraphAgentLoop budget denied for job %s: %s",
                    job.job_id, denial,
                )
                raise RuntimeError(
                    f"LangGraph agent loop budget exhausted for job "
                    f"{job.job_id}: {denial}"
                )

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

        cumulative_tokens = 0
        for msg in output_messages:
            usage = getattr(msg, "usage_metadata", {}) or {}
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            cumulative_tokens += input_tokens + output_tokens
        if cumulative_tokens > self._max_total_tokens:
            logger.warning(
                "LangGraphAgentLoop token limit exceeded for job %s: %d > %d",
                job.job_id, cumulative_tokens, self._max_total_tokens,
            )
            raise RuntimeError(
                f"LangGraph agent loop total tokens {cumulative_tokens} exceeded "
                f"limit {self._max_total_tokens} for job {job.job_id}"
            )

        final_content = ""
        for msg in reversed(output_messages):
            if hasattr(msg, "content") and msg.content and getattr(msg, "type", "") == "ai":
                final_content = str(msg.content)
                break

        if self._adversarial_detector is not None and final_content:
            scan_result = self._adversarial_detector.scan_text(
                final_content, file_path=f"langgraph_agent:{job.job_id}"
            )
            if scan_result.blocked:
                logger.warning(
                    "LangGraphAgentLoop adversarial scan blocked output "
                    "for job %s: %s",
                    job.job_id, scan_result.summary,
                )
                raise RuntimeError(
                    f"LangGraph agent loop output blocked by adversarial "
                    f"scan for job {job.job_id}: {scan_result.summary}"
                )

        if final_content:
            return final_content

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
        """Convert MCP tools to LangChain tools with capability gates + timeouts.

        When a tool_auditor is configured, each tool invocation is gated
        through ``auditor.audit()`` before execution, and
        ``record_success()`` / ``record_error()`` is called after.
        """
        if self._mcp_client is None:
            return []

        from langchain_core.tools import StructuredTool

        mcp_tools = await self._mcp_client.list_tools()
        langchain_tools: list[Any] = []

        for mcp_tool in mcp_tools:
            tool_name = mcp_tool.name
            tool_desc = mcp_tool.description or f"MCP tool: {tool_name}"
            tool_schema = getattr(mcp_tool, "input_schema", None)
            server_id = self._resolve_server_id(tool_name)

            mcp_client = self._mcp_client
            timeout = self._per_tool_timeout
            auditor = self._auditor
            sandbox_enforcer = self._sandbox_enforcer

            async def _execute(
                *args: Any,
                _tool_name: str = tool_name,
                _server_id: str = server_id,
                _client: Any = mcp_client,
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
                    except SandboxNotAvailableError as exc:
                        return (
                            f"Tool error: sandbox not available — "
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
                            f"Tool error: tool call blocked by auditor: "
                            f"{verdict.classification}. "
                            f"{verdict.reason} "
                            f"Do not retry this call. Use a different approach."
                        )
                try:
                    result = await asyncio.wait_for(
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

            lc_tool = StructuredTool.from_function(
                coroutine=_execute,
                name=tool_name,
                description=tool_desc,
                args_schema=tool_schema or None,
            )
            langchain_tools.append(lc_tool)

        return langchain_tools
