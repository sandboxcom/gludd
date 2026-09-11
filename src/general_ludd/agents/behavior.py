"""Agent behavior configuration and system prompt rendering.

Codifies all behavioral rules as structured, serializable config that
gets rendered into agent system prompts at runtime.
"""

from __future__ import annotations

from general_ludd.agents.behavior_contracts import AgentBehavior, GuardrailConfig


class BehaviorRenderer:
    """Renders an :class:`AgentBehavior` into a system-prompt string.

    P4 perf fix: the behavior body produced by :meth:`render` is fully
    determined by the behavior's render-relevant fields and is invariant across
    dispatches, yet ``render_as_prompt`` (and therefore the per-dispatch path
    ``AgentRegistry.render_behavior_prompt``) used to rebuild the entire
    multi-section string from scratch on EVERY invocation. We now memoize the
    rendered body keyed by the behavior's render-determining content so it is
    built once per distinct behavior and reused. The cache key is content-based
    (not object identity) so distinct behaviors are never conflated and two
    equivalent behaviors share one entry. ``assumption_log`` is excluded from
    the key because :meth:`render` never reads it — including it would defeat
    the cache as assumptions accumulate without changing the output.
    """

    def __init__(
        self,
        prompt_enhancer: object | None = None,
        skill_context_provider: object | None = None,
    ) -> None:
        """Create a renderer with optional prompt and skill-context adapters."""
        # Cache of rendered behavior bodies keyed by render-determining content.
        self._render_cache: dict[str, str] = {}
        self._prompt_enhancer = prompt_enhancer
        self._skill_context_provider = skill_context_provider

    @staticmethod
    def _cache_key(behavior: AgentBehavior) -> str:
        """Stable key over the fields that actually affect render() output.

        Excludes ``assumption_log`` (never read by render) so the cache stays
        effective as assumptions accumulate. ``model_dump_json`` with sorted
        keys gives a deterministic, hashable representation.
        """
        return behavior.model_dump_json(exclude={"assumption_log"})

    def render(self, behavior: AgentBehavior) -> str:
        """Render the stable policy sections for one behavior contract."""
        key = self._cache_key(behavior)
        cached = self._render_cache.get(key)
        if cached is not None:
            return cached
        rendered = self._render_uncached(behavior)
        self._render_cache[key] = rendered
        return rendered

    def _render_uncached(self, behavior: AgentBehavior) -> str:
        sections: list[str] = ["# Agent Behavior Configuration"]
        sections.append("")

        if behavior.role or behavior.goal:
            sections.append("## Role and Goal")
            if behavior.role:
                sections.append(f"Role: {behavior.role}")
            if behavior.goal:
                sections.append(f"Goal: {behavior.goal}")
            sections.append("")

        sections.append("## Task Completion")
        if behavior.completion_policy == "complete_all":
            sections.append(
                "You MUST complete ALL requested work before stopping. No exceptions."
            )
            sections.append(
                "Do NOT stop early to report status. Do NOT pause to ask if the user wants you to continue."
            )
            sections.append(
                "After completing one objective, immediately start the next. No victory laps."
            )
        else:
            sections.append(
                "Complete work until you hit a blocker you cannot resolve."
            )
        sections.append("")

        if behavior.self_directed_work:
            sections.append("## Self-Directed Work")
            sections.append(
                "When you identify a gap, bug, or missing integration while working, "
                "you MUST fix it immediately. Do NOT stop to ask whether to proceed."
            )
            sections.append(
                "If you found it, you own it. Fix it, test it, commit it, then continue."
            )
            sections.append("")

        if behavior.prefer_automated_tools:
            sections.append("## Automated Tooling Over Manual Code Walks")
            sections.append(
                "You MUST prefer automated tools (make targets, ruff plugins, ansible roles, "
                "shell scripts) over manually walking through code with grep/read loops."
            )
            sections.append(
                "When you need to find issues in the codebase, WRITE A SCRIPT or make target "
                "that does the work mechanically, then rely on its output. Do NOT spend agent "
                "context/tokens on serial grep→read→analyze loops."
            )
            sections.append(
                "A one-time investment in an automated checker pays off on every future use. "
                "Manual code walks cost tokens every time and miss patterns."
            )
            sections.append("")
            sections.append(
                "Before writing new code to parse a format, ingest data, or perform a common "
                "operation, dispatch a RESEARCH CHECK FIRST: does an existing library, module, "
                "CLI tool, ansible collection, or code in this repo already solve this? "
                "Never write new code for a problem that has a mature OSS solution."
            )
            sections.append("")

        if behavior.tdd_enforced:
            sections.append("## TDD Policy")
            sections.append(
                "You MUST write a failing test BEFORE writing implementation code."
            )
            sections.append(
                "Workflow: write failing test -> confirm it fails -> implement -> confirm it passes."
            )
            sections.append("")

        if behavior.commit_after_green:
            sections.append("## Commit-After-Green")
            sections.append(
                "You MUST commit your work after tests pass and the change is complete."
            )
            sections.append(
                "Do not leave green work uncommitted."
            )
            sections.append("")

        if behavior.evidence_required:
            sections.append("## Evidence-Based Responses")
            sections.append(
                "Every factual claim MUST have supporting evidence from a tool call, "
                "file read, URL fetch, or test result."
            )
            sections.append(
                "Unsupported claims are policy violations."
            )
            sections.append("")

        if behavior.atomic_commits:
            sections.append("## Atomic Commits")
            sections.append(
                "Each commit must represent one logical change. "
                "Never batch unrelated changes into a single commit."
            )
            sections.append("")

        if behavior.session_persistence:
            sections.append("## Session Persistence")
            sections.append(
                "You MUST maintain SESSION.md at the project root. "
                "Read it at session start to restore context."
            )
            sections.append(
                "Update it after every logical unit of work. Never leave it stale."
            )
            sections.append("")

        if behavior.guardrail_layers > 0:
            sections.append("## Guardrail Policy")
            layers: list[str] = []
            if behavior.guardrail.config_layer:
                layers.append("Config permission (hard gate)")
            if behavior.guardrail.hook_layer:
                layers.append("Runtime hook (contextual error)")
            if behavior.guardrail.prompt_layer:
                layers.append("Agent prompt (proactive instruction)")
            sections.append(
                f"Every new restriction must be enforced at {behavior.guardrail_layers} layer(s):"
            )
            for layer in layers:
                sections.append(f"- {layer}")
            sections.append("")

        if behavior.allowed_command_patterns:
            sections.append("## Command Policy")
            patterns = ", ".join(f"`{p}`" for p in behavior.allowed_command_patterns)
            sections.append(f"Allowed command patterns: {patterns}.")
            sections.append("")

        if behavior.stop_conditions:
            sections.append("## Stop Conditions")
            sections.append("Stop work immediately if:")
            for cond in behavior.stop_conditions:
                sections.append(f"- {cond}")
            sections.append("")

        if behavior.self_improve_interval > 0:
            sections.append("## Self-Improvement Cycle")
            sections.append(
                f"Every {behavior.self_improve_interval} ticks, run self-improvement analysis "
                "to discover gaps and create fix todos autonomously."
            )
            sections.append(
                "Gaps found are enqueued as high-priority self_improve todos."
            )
            sections.append("")

        if behavior.never_block_on_questions:
            sections.append("## Never Block On Questions")
            sections.append(
                "Never pause work to ask the user a question. Default to action: make a "
                "reasonable assumption, state it explicitly, and keep going."
            )
            sections.append(
                "Only stop for a stop_condition (missing credentials or irreversible destructive action)."
            )
            sections.append("")

        if behavior.repair_not_disable:
            sections.append("## Fix Means Repair, Never Disable")
            sections.append(
                "When something fails, repair the root cause. Do NOT disable, comment-out, "
                "skip, xfail, or delete the feature or test to make the gate green."
            )
            sections.append(
                "A disable is only legitimate as an explicitly tracked decision with a "
                "follow-up todo — never a silent one."
            )
            sections.append("")

        if behavior.assume_and_proceed:
            sections.append("## No-Blocking-Questions Policy")
            sections.append(
                "NEVER pause work to ask the user a clarifying question. "
                "Default to action: state your assumption, record it, and proceed."
            )
            sections.append(
                "Use record_assumption() to log decisions. Reserve blocking only for truly irreversible choices."
            )
            sections.append("")

        if behavior.subagent_context_limit_lines > 0:
            sections.append("## Subagent Context Limit")
            sections.append(
                f"Return ≤{behavior.subagent_context_limit_lines} lines. "
                "Do NOT dump large file contents into your response. "
                "Read files you need and summarize."
            )
            sections.append("")

        return "\n".join(sections)

    def render_as_prompt(
        self, behavior: AgentBehavior, agent_name: str, task: str
    ) -> str:
        """Render behavior plus the task-bound agent and optional skill context."""
        base = self.render(behavior)
        header = (
            f"You are agent **{agent_name}**. Your current task: {task}\n\n"
        )
        sections: list[str] = [header]

        if self._skill_context_provider is not None:
            provider = getattr(self._skill_context_provider, "provide", None)
            if provider is not None:
                try:
                    ctx = provider(task)
                    if ctx.context_text:
                        sections.append(
                            "# Available Skill Context (lensed for this task)\n\n"
                            f"{ctx.context_text}\n"
                            f"_Token savings: ~{ctx.token_savings} tokens "
                            f"(vs full skills)_\n"
                        )
                except Exception:
                    pass

        sections.append(base)
        result = "\n".join(sections)
        if self._prompt_enhancer is not None:
            enhance = getattr(self._prompt_enhancer, "enhance_prompt", None)
            if enhance is not None:
                result = enhance(result)
        return result


def default_primary_behavior() -> AgentBehavior:
    """Return the fail-closed behavior used by the primary orchestrator."""
    return AgentBehavior(
        role="primary orchestrator",
        goal="coordinate and complete the requested work",
        completion_policy="complete_all",
        self_directed_work=True,
        tdd_enforced=True,
        commit_after_green=True,
        evidence_required=True,
        atomic_commits=True,
        session_persistence=True,
        guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True),
        allowed_command_patterns=["make *"],
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
        repair_not_disable=True,
    )


def default_subagent_behavior() -> AgentBehavior:
    """Return the bounded behavior used by delegated execution agents."""
    return AgentBehavior(
        role="specialized subagent",
        goal="execute the assigned task and return evidence",
        completion_policy="complete_all",
        self_directed_work=False,
        tdd_enforced=True,
        commit_after_green=True,
        evidence_required=True,
        atomic_commits=True,
        session_persistence=True,
        guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True),
        allowed_command_patterns=["make *"],
        stop_conditions=["missing_credentials", "environment_change"],
        never_block_on_questions=True,
        repair_not_disable=True,
    )


__all__ = (
    "AgentBehavior",
    "BehaviorRenderer",
    "GuardrailConfig",
    "default_primary_behavior",
    "default_subagent_behavior",
)
