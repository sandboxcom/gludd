"""Agent capabilities bundle — the model-facing helpers used on the generation
path.

Bundles the context/token/tool/failover helpers behind one object so the worker
generation path (``worker/app.py:_invoke_gateway_for_job``) can bound prompts,
track token budgets, expose registered agents as dispatch tools, and run a
tool-call loop when an MCP client is present — without each call site wiring the
pieces individually.
"""

from __future__ import annotations

from typing import Any

from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.token_window import TokenWindowManager
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.execution.tool_loop import ToolCallLoop
from general_ludd.models.failover import ModelFailoverChain


class AgentCapabilities:
    def __init__(
        self,
        max_tokens: int = 128000,
        compaction_threshold: float = 0.8,
        preserve_recent_count: int = 4,
        primary_profile: str = "default",
        fallback_profiles: list[str] | None = None,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        self.compactor = ContextCompactor(
            max_tokens=max_tokens,
            compaction_threshold=compaction_threshold,
            preserve_recent_count=preserve_recent_count,
        )
        self.token_window = TokenWindowManager(default_budget=max_tokens)
        self._registry = agent_registry or AgentRegistry()
        self.tool_adapter = AgentToolAdapter(self._registry)
        self.failover = ModelFailoverChain(
            primary_profile=primary_profile,
            fallback_profiles=fallback_profiles or [],
        )

    def prepare_messages(
        self, system_prompt: str, history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Compact the conversation history via ContextCompactor.

        Attempts to fit the prompt within the configured token budget by
        dropping older turns when the compaction threshold is exceeded.
        NOTE: an oversized system prompt or a single preserved message that
        individually exceeds the budget passes through uncapped — the compactor
        cannot split individual messages. Callers that need a hard cap must
        truncate the system prompt before calling this method.
        """
        msgs: list[ContextMessage] = [
            ContextMessage(
                role="system",
                content=system_prompt,
                token_estimate=self.compactor.estimate_tokens(system_prompt),
                is_system=True,
            )
        ]
        for turn in history:
            content = turn.get("content", "")
            msgs.append(
                ContextMessage(
                    role=turn.get("role", "user"),
                    content=content,
                    token_estimate=self.compactor.estimate_tokens(content),
                    is_system=turn.get("role") == "system",
                )
            )
        compacted = self.compactor.compact(msgs)
        return [{"role": m.role, "content": m.content} for m in compacted]

    def within_budget(
        self, agent_name: str, prompt: str, max_tokens: int | None = None
    ) -> bool:
        cap = max_tokens if max_tokens is not None else self.token_window.get_remaining_budget(agent_name)
        return self.token_window.check_budget(agent_name, prompt, cap)

    def list_agent_tools(self) -> list[dict[str, str]]:
        return self.tool_adapter.list_agent_tools()

    def make_tool_loop(
        self,
        model_gateway: Any,
        mcp_client: Any = None,
        mcp_registry: Any = None,
    ) -> ToolCallLoop:
        # mcp_registry pins the capability gate (Finding 3) explicitly; if not
        # passed, ToolCallLoop falls back to the client's own registry.
        return ToolCallLoop(
            model_gateway=model_gateway,
            mcp_client=mcp_client,
            mcp_registry=mcp_registry,
        )

    def make_graph_gateway(
        self,
        model_gateway: Any,
        adaptive_router: Any = None,
        benchmark_repo: Any = None,
        enable_graph: bool = True,
    ) -> Any:
        """Build a multi-step LangGraphGateway scored by PromptScoringEngine.

        Generate -> score -> retry-or-return. Falls back to single-shot when
        langgraph isn't installed or ``enable_graph`` is False.
        """
        from general_ludd.models.langgraph_gateway import LangGraphGateway
        from general_ludd.scoring.engine import PromptScoringEngine

        scoring = PromptScoringEngine(
            model_gateway=model_gateway, benchmark_repo=benchmark_repo
        )
        return LangGraphGateway(
            call_model_fn=getattr(model_gateway, "call_model", None),
            adaptive_router=adaptive_router,
            scoring_engine=scoring,
            enable_graph=enable_graph,
        )
