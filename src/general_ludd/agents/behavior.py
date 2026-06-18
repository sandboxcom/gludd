"""Agent behavior configuration and system prompt rendering.

Codifies all behavioral rules as structured, serializable config that
gets rendered into agent system prompts at runtime.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from pydantic import BaseModel, field_validator

# Shell metacharacters that enable chaining / redirection / substitution. A
# command containing any of these is rejected outright: the allowlist matches a
# SINGLE command (e.g. "make test"), and these characters would let a matched
# prefix smuggle a second, unmatched command (e.g. "make x; rm -rf /").
_SHELL_METACHARACTERS = frozenset(";&|<>`$()\n\r")


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
    self_improve_interval: int = 0
    never_block_on_questions: bool = True
    repair_not_disable: bool = True

    @field_validator("max_retries")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_retries must be non-negative")
        return v

    @field_validator("self_improve_interval")
    @classmethod
    def _non_negative_interval(cls, v: int) -> int:
        if v < 0:
            raise ValueError("self_improve_interval must be non-negative")
        return v

    @property
    def guardrail_layers(self) -> int:
        return self.guardrail.layer_count()

    def should_stop(self, condition: str) -> bool:
        return condition in self.stop_conditions

    def is_command_allowed(self, command: str) -> bool:
        """Enforceable predicate over ``allowed_command_patterns``.

        ``allowed_command_patterns`` was previously prompt-only (rendered into the
        system prompt, never actually checked). This makes it a real gate so it CAN
        be enforced. NOTE: the live mechanism in this repo is still EXTERNAL —
        ``.opencode/plugin/enforce-make.ts`` and the harness permission rules deny
        non-``make`` Bash before it runs. This predicate gives an in-process,
        testable equivalent for callers that want to pre-check a command.

        A command is allowed only if ALL hold:
          - it is non-empty after stripping,
          - it contains no shell metacharacter (chaining/redirect/substitution),
          - it does not begin with ``-`` (would be parsed as an option),
          - it carries no ``VAR=val`` env prefix (smuggles environment), and
          - it fnmatch-matches at least one allowed pattern.
        """
        if not command or not command.strip():
            return False
        cmd = command.strip()
        # Reject shell metacharacters: they enable chaining/redirection so a
        # matched prefix could carry an unmatched second command.
        if any(ch in _SHELL_METACHARACTERS for ch in cmd):
            return False
        first = cmd.split(" ", 1)[0]
        # Leading-dash first token would be parsed as an option, not a command.
        if first.startswith("-"):
            return False
        # An env-prefix (FOO=bar make test) smuggles environment into the call.
        if "=" in first:
            return False
        return any(
            fnmatch.fnmatch(cmd, pattern) for pattern in self.allowed_command_patterns
        )

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
        never_block_on_questions=True,
        repair_not_disable=True,
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
        never_block_on_questions=True,
        repair_not_disable=True,
    )
