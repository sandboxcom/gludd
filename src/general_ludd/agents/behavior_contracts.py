"""Serializable agent behavior and enforceable command-policy contracts."""

from __future__ import annotations

import fnmatch

from pydantic import BaseModel, Field, field_validator

_SHELL_METACHARACTERS = frozenset(";&|<>`$()\n\r")


class GuardrailConfig(BaseModel):
    """Select the independently enforced guardrail layers."""

    config_layer: bool = True
    hook_layer: bool = True
    prompt_layer: bool = True

    def layer_count(self) -> int:
        """Return the number of enabled enforcement layers."""
        return sum([self.config_layer, self.hook_layer, self.prompt_layer])

    def ensure_valid(self) -> None:
        """Reject a configuration that disables every guardrail layer."""
        if self.layer_count() == 0:
            raise ValueError("At least one guardrail layer must be enabled")

    def model_post_init(self, __context: object) -> None:
        """Validate the complete Pydantic model after construction."""
        self.ensure_valid()


class AgentBehavior(BaseModel):
    """Structured behavioral policy rendered into agent system prompts."""

    role: str | None = None
    goal: str | None = None
    backstory: str | None = None
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
    prefer_automated_tools: bool = True
    research_before_build: bool = True
    assume_and_proceed: bool = True
    assumption_log: list[str] = Field(default_factory=list)
    subagent_context_limit_lines: int = 10

    @field_validator("max_retries")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_retries must be non-negative")
        return value

    @field_validator("self_improve_interval")
    @classmethod
    def _non_negative_interval(cls, value: int) -> int:
        if value < 0:
            raise ValueError("self_improve_interval must be non-negative")
        return value

    @property
    def guardrail_layers(self) -> int:
        """Return the active enforcement-layer count."""
        return self.guardrail.layer_count()

    def should_stop(self, condition: str) -> bool:
        """Return whether a typed condition requires work to stop."""
        return condition in self.stop_conditions

    def is_command_allowed(self, command: str) -> bool:
        """Require one non-option, non-environment, metacharacter-free command."""
        if not command or not command.strip():
            return False
        normalized = command.strip()
        if any(character in _SHELL_METACHARACTERS for character in normalized):
            return False
        first = normalized.split(" ", 1)[0]
        if first.startswith("-") or "=" in first:
            return False
        return any(
            fnmatch.fnmatch(normalized, pattern)
            for pattern in self.allowed_command_patterns
        )

    def record_assumption(self, question: str, assumed_answer: str) -> str:
        """Record an explicit assumption and return its rendered form."""
        entry = f"ASSUMPTION: {question} → assumed: {assumed_answer}"
        self.assumption_log.append(entry)
        return entry

    def should_block_on_question(self, question: str) -> bool:
        """Return whether the configured policy permits a blocking question."""
        return not self.assume_and_proceed

    def to_dict(self) -> dict[str, object]:
        """Serialize the behavior with Pydantic's canonical representation."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AgentBehavior:
        """Validate and construct a behavior from serialized configuration."""
        return cls.model_validate(data)


__all__ = ["AgentBehavior", "GuardrailConfig"]
