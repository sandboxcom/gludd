"""Agent capabilities bundle — the model-facing helpers used on the generation
path.

Bundles the context/token/tool/failover helpers behind one object so the worker
generation path (``worker/app.py:_invoke_gateway_for_job``) can bound prompts,
track token budgets, expose registered agents as dispatch tools, and run a
tool-call loop when an MCP client is present — without each call site wiring the
pieces individually.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.mcp.registry import MCPToolRegistry

if TYPE_CHECKING:
    from general_ludd.compaction.aggressive import CompactionLevel
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.token_window import TokenWindowManager
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
from general_ludd.execution.tool_loop import ToolCallLoop
from general_ludd.models.failover import ModelFailoverChain
from general_ludd.security.capability_lattice import _BUILTIN as _KNOWN_ROLES


class AgentCapabilities:
    def __init__(
        self,
        max_tokens: int = 128000,
        compaction_threshold: float = 0.8,
        preserve_recent_count: int = 4,
        primary_profile: str = "default",
        fallback_profiles: list[str] | None = None,
        agent_registry: AgentRegistry | None = None,
        model_gateway: object = None,
        use_slm_compaction: bool = False,
        compaction_level: CompactionLevel | None = None,
    ) -> None:
        self.compactor = ContextCompactor(
            max_tokens=max_tokens,
            compaction_threshold=compaction_threshold,
            preserve_recent_count=preserve_recent_count,
        )
        self._preserve_recent_count = preserve_recent_count
        self._compaction_threshold = compaction_threshold
        self._max_tokens = max_tokens
        # Optional aggression rung (compaction/aggressive.LEVELS). None → the
        # historical behavior (threshold/preserve_recent from the ctor args).
        self._compaction_level = compaction_level
        # OPT-IN local-SLM compaction. Default OFF and gateway=None → the plain
        # ContextCompactor path is used, so no existing call site changes
        # behavior. When a gateway is supplied AND the flag is on, prepare_messages
        # routes the older middle of the conversation through a small local model
        # (the ``compactor`` profile) which fails SOFT to extractive truncation if
        # the profile is missing or the gateway errors — compaction never crashes
        # the context path.
        self._slm_compactor: object = None
        self._slm_summarize_fn: object = None
        if model_gateway is not None and use_slm_compaction:
            from general_ludd.compaction.slm import (
                SLMCompactor,
                make_slm_summarize_fn,
            )

            # Use the level's preserve_recent when an aggression rung is set,
            # else the ctor default.
            preserve = (
                compaction_level.preserve_recent
                if compaction_level is not None
                else preserve_recent_count
            )
            self._slm_summarize_fn = make_slm_summarize_fn(model_gateway, "compactor")
            self._slm_compactor = SLMCompactor(
                summarize_fn=self._slm_summarize_fn,
                preserve_recent=preserve,
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
        # Opt-in SLM path: route through compaction.aggressive.compact_messages so
        # the hard fail-soft wrapper (compaction can never raise into this caller)
        # and the aggression level are shared with the generation-path callers.
        # compact_messages does its own threshold gating, so short conversations
        # behave identically to the default path. Behavior is UNCHANGED when
        # _slm_compactor is None (the plain ContextCompactor path below runs).
        if self._slm_compactor is not None:
            from general_ludd.compaction.aggressive import (
                CompactionLevel,
                compact_messages,
            )

            level = self._compaction_level or CompactionLevel(
                preserve_recent=self._preserve_recent_count,
                threshold=self._compaction_threshold,
            )
            compacted_msgs = compact_messages(
                msgs,
                goal="",
                level=level,
                summarize_fn=cast(Callable[[str, str], str] | None, self._slm_summarize_fn),
                max_tokens=self._max_tokens,
            )
            return [{"role": m.role, "content": m.content} for m in compacted_msgs]
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
        model_gateway: object,
        mcp_client: object = None,
        mcp_registry: object = None,
        budget_guard: object = None,
        adversarial_detector: object = None,
        max_total_tokens: int | None = None,
        per_iteration_timeout: float | None = None,
        work_type_max_iterations: dict[str, int] | None = None,
    ) -> ToolCallLoop:
        # mcp_registry pins the capability gate (Finding 3) explicitly; if not
        # passed, ToolCallLoop falls back to the client's own registry.
        #
        # SLICE 2 (task #56): thread the SAME opt-in SLM compaction config into the
        # ITERATIVE tool loop so long tool conversations send fewer tokens per
        # model call. Only wired when SLM compaction is actually enabled on this
        # bundle (``_slm_compactor is not None``); otherwise both stay None so the
        # tool loop is byte-for-byte identical to today (default OFF). When enabled
        # without an explicit rung we synthesize one from the ctor threshold /
        # preserve_recent, mirroring ``prepare_messages``.
        tool_compaction_level = None
        tool_summarize_fn = None
        if self._slm_compactor is not None:
            from general_ludd.compaction.aggressive import CompactionLevel

            tool_compaction_level = self._compaction_level or CompactionLevel(
                preserve_recent=self._preserve_recent_count,
                threshold=self._compaction_threshold,
            )
            tool_summarize_fn = self._slm_summarize_fn
        return ToolCallLoop(
            model_gateway=model_gateway,
            mcp_client=mcp_client,
            mcp_registry=cast(MCPToolRegistry | None, mcp_registry),
            compaction_level=tool_compaction_level,
            summarize_fn=cast(Callable[[str, str], str] | None, tool_summarize_fn),
            budget_guard=budget_guard,
            adversarial_detector=adversarial_detector,
            max_total_tokens=max_total_tokens,
            per_iteration_timeout=per_iteration_timeout,
            work_type_max_iterations=work_type_max_iterations,
        )

    def make_langgraph_tool_loop(
        self,
        model_gateway: object,
        role: str,
        mcp_client: object = None,
        mcp_registry: object = None,
    ) -> LangGraphAgentLoop:
        """Build a LangGraphAgentLoop backed by ``create_react_agent``.

        Resolves the LangChain chat model from the gateway via
        ``get_chat_model`` and returns a loop that delegates the
        model→tool→model cycle to langgraph's native agent runtime.

        Per-role capability gating and server_id resolution are preserved
        inside the returned loop.  SLM compaction and tool auditing are
        noted as follow-up items (see ``langgraph_agent.py``).

        ``role`` is REQUIRED — the loop's capability gate (``check_dispatch``)
        uses it to enforce per-role dispatch permissions.  Only known roles
        from the capability lattice are accepted.
        """
        if not role or not isinstance(role, str) or not role.strip():
            raise ValueError(
                f"make_langgraph_tool_loop: role is required, got {role!r}"
            )
        role = role.strip()
        if role not in _KNOWN_ROLES:
            raise ValueError(
                f"make_langgraph_tool_loop: unknown role {role!r}; "
                f"valid roles: {sorted(_KNOWN_ROLES.keys())}"
            )
        return LangGraphAgentLoop(
            model_gateway=model_gateway,
            chat_model=None,
            mcp_client=mcp_client,
            mcp_registry=cast(MCPToolRegistry | None, mcp_registry),
            role=role,
        )

    def make_graph_gateway(
        self,
        model_gateway: object,
        adaptive_router: object = None,
        benchmark_repo: object = None,
        enable_graph: bool = True,
    ) -> object:
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
