"""Agent behavior configuration and system prompt rendering.

Codifies all behavioral rules as structured, serializable config that
gets rendered into agent system prompts at runtime.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GuardrailConfig(BaseModel):
    config_layer: bool = True
    hook_layer: bool = True
    prompt_layer: bool = True

    def layer_count(self) -> int:
        return sum([self.config_layer, self.hook_layer, self.prompt_layer])

    def ensure_valid(self) -> None:
        if self.layer_count() == 0:
            raise ValueError("At least one guardrail layer must be enabled")

    def model_post_init(self, __context: object) -> None:
        self.ensure_valid()


class AgentBehavior(BaseModel):
    completion_policy: str = "complete_all"
    self_directed_work: bool = True
    tdd_enforced: bool = True
    commit_after_green: bool = True
    evidence_required: bool = True
    atomic_commits: bool = True
    session_persistence: bool = True
    guardrail: GuardrailConfig = GuardrailConfig()
    allowed_command_patterns: list[str] = ["make *"]
    stop_conditions: list[str] = ["missing_credentials", "environment_change"]
    max_retries: int = 3

    @property
    def guardrail_layers(self) -> int:
        return self.guardrail.layer_count()

    def should_stop(self, condition: str) -> bool:
        return condition in self.stop_conditions

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentBehavior:
        return cls(**data)


class BehaviorRenderer:
    def render(self, behavior: AgentBehavior) -> str:
        sections: list[str] = ["# Agent Behavior Configuration"]
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

        return "\n".join(sections)

    def render_as_prompt(
        self, behavior: AgentBehavior, agent_name: str, task: str
    ) -> str:
        base = self.render(behavior)
        header = f"You are agent **{agent_name}**. Your current task: {task}\n\n"
        return header + base


def default_primary_behavior() -> AgentBehavior:
    return AgentBehavior(
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
    )


def default_subagent_behavior() -> AgentBehavior:
    return AgentBehavior(
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
    )
