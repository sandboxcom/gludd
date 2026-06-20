"""Unit tests for behavior.py — covering gaps not in existing test suite.

Covers:
- is_command_allowed: 6 cases (metachar, dash, env-prefix, empty, non-match, custom)
- GuardrailConfig all-False raises ValueError
- BehaviorRenderer completion_policy != "complete_all" (else-branch)
- AgentBehavior from_dict / to_dict round-trip
- AgentBehavior._non_negative validator (max_retries < 0 raises)
- AgentBehavior._non_negative_interval validator (self_improve_interval < 0 raises)
- AgentBehavior.guardrail_layers property
- AgentBehavior.should_stop
- AgentBehavior.record_assumption + should_block_on_question
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
)

# ---------------------------------------------------------------------------
# GuardrailConfig
# ---------------------------------------------------------------------------

class TestGuardrailConfigAllFalse:
    def test_all_false_raises_value_error(self) -> None:
        """GuardrailConfig(all False) must raise ValueError via model_post_init."""
        with pytest.raises(ValueError, match="At least one guardrail"):
            GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False)

    def test_layer_count_full(self) -> None:
        g = GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True)
        assert g.layer_count() == 3

    def test_layer_count_partial(self) -> None:
        g = GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=True)
        assert g.layer_count() == 2

    def test_ensure_valid_raises_on_zero(self) -> None:
        """ensure_valid() raises explicitly when called on a zero-count config."""
        g = GuardrailConfig.__new__(GuardrailConfig)
        # Bypass __init__ to craft a zero-count state for ensure_valid() isolation
        object.__setattr__(g, "config_layer", False)
        object.__setattr__(g, "hook_layer", False)
        object.__setattr__(g, "prompt_layer", False)
        with pytest.raises(ValueError):
            g.ensure_valid()


# ---------------------------------------------------------------------------
# is_command_allowed — 6 cases
# ---------------------------------------------------------------------------

class TestIsCommandAllowed:
    def _behavior(self, patterns: list[str] | None = None) -> AgentBehavior:
        return AgentBehavior(
            allowed_command_patterns=patterns if patterns is not None else ["make *"],
        )

    def test_metachar_rejected(self) -> None:
        """A command containing a shell metacharacter is rejected."""
        b = self._behavior()
        assert b.is_command_allowed("make test; rm -rf /") is False

    def test_leading_dash_rejected(self) -> None:
        """A command whose first token starts with '-' is rejected."""
        b = self._behavior()
        assert b.is_command_allowed("-v make test") is False

    def test_env_prefix_rejected(self) -> None:
        """A command with VAR=val as the first token is rejected."""
        b = self._behavior()
        assert b.is_command_allowed("FOO=bar make test") is False

    def test_empty_string_rejected(self) -> None:
        """An empty command string is rejected."""
        b = self._behavior()
        assert b.is_command_allowed("") is False

    def test_whitespace_only_rejected(self) -> None:
        """A whitespace-only command is rejected."""
        b = self._behavior()
        assert b.is_command_allowed("   ") is False

    def test_non_matching_pattern_rejected(self) -> None:
        """A valid command that doesn't match any allowed pattern is rejected."""
        b = self._behavior(patterns=["make *"])
        assert b.is_command_allowed("git status") is False

    def test_matching_custom_pattern_allowed(self) -> None:
        """Custom pattern is honoured: 'git *' allows 'git status'."""
        b = self._behavior(patterns=["git *"])
        assert b.is_command_allowed("git status") is True

    def test_make_target_allowed(self) -> None:
        """Standard 'make test' is permitted under default pattern."""
        b = self._behavior()
        assert b.is_command_allowed("make test") is True

    def test_pipe_metachar_rejected(self) -> None:
        """Pipe character counts as a shell metacharacter."""
        b = self._behavior()
        assert b.is_command_allowed("make test | grep pass") is False

    def test_backtick_metachar_rejected(self) -> None:
        """Backtick counts as a shell metacharacter."""
        b = self._behavior()
        assert b.is_command_allowed("make `echo test`") is False


# ---------------------------------------------------------------------------
# BehaviorRenderer — else-branch for completion_policy
# ---------------------------------------------------------------------------

class TestBehaviorRendererCompletionPolicy:
    def test_complete_all_contains_must_complete(self) -> None:
        """'complete_all' renders the strict completion language."""
        r = BehaviorRenderer()
        b = AgentBehavior(completion_policy="complete_all")
        rendered = r.render(b)
        assert "MUST complete ALL" in rendered

    def test_non_complete_all_hits_else_branch(self) -> None:
        """Any policy other than 'complete_all' renders the else-branch text."""
        r = BehaviorRenderer()
        b = AgentBehavior(completion_policy="partial")
        rendered = r.render(b)
        assert "Complete work until you hit a blocker" in rendered
        assert "MUST complete ALL" not in rendered

    def test_render_as_prompt_includes_agent_name(self) -> None:
        """render_as_prompt includes the agent name and task in the output."""
        r = BehaviorRenderer()
        b = AgentBehavior()
        result = r.render_as_prompt(b, agent_name="myagent", task="fix the bug")
        assert "myagent" in result
        assert "fix the bug" in result

    def test_render_self_improve_interval_positive(self) -> None:
        """A positive self_improve_interval renders the self-improvement section."""
        r = BehaviorRenderer()
        b = AgentBehavior(self_improve_interval=10)
        rendered = r.render(b)
        assert "Self-Improvement Cycle" in rendered
        assert "10 ticks" in rendered

    def test_render_self_improve_interval_zero_skipped(self) -> None:
        """self_improve_interval=0 means the section is NOT rendered."""
        r = BehaviorRenderer()
        b = AgentBehavior(self_improve_interval=0)
        rendered = r.render(b)
        assert "Self-Improvement Cycle" not in rendered


# ---------------------------------------------------------------------------
# AgentBehavior from_dict / to_dict
# ---------------------------------------------------------------------------

class TestAgentBehaviorRoundTrip:
    def test_to_dict_returns_dict(self) -> None:
        b = AgentBehavior()
        d = b.to_dict()
        assert isinstance(d, dict)
        assert "completion_policy" in d
        assert "max_retries" in d

    def test_from_dict_round_trip(self) -> None:
        b = AgentBehavior(
            completion_policy="partial",
            max_retries=2,
            tdd_enforced=False,
        )
        d = b.to_dict()
        b2 = AgentBehavior.from_dict(d)
        assert b2.completion_policy == "partial"
        assert b2.max_retries == 2
        assert b2.tdd_enforced is False

    def test_from_dict_defaults(self) -> None:
        """from_dict with minimal keys produces a valid AgentBehavior."""
        b = AgentBehavior.from_dict({})
        assert b.completion_policy == "complete_all"
        assert b.max_retries == 3


# ---------------------------------------------------------------------------
# AgentBehavior validators
# ---------------------------------------------------------------------------

class TestAgentBehaviorValidators:
    def test_max_retries_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            AgentBehavior(max_retries=-1)

    def test_max_retries_zero_allowed(self) -> None:
        b = AgentBehavior(max_retries=0)
        assert b.max_retries == 0

    def test_self_improve_interval_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            AgentBehavior(self_improve_interval=-1)

    def test_self_improve_interval_zero_allowed(self) -> None:
        b = AgentBehavior(self_improve_interval=0)
        assert b.self_improve_interval == 0


# ---------------------------------------------------------------------------
# AgentBehavior auxiliary methods
# ---------------------------------------------------------------------------

class TestAgentBehaviorMethods:
    def test_guardrail_layers_property(self) -> None:
        b = AgentBehavior(
            guardrail=GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=True)
        )
        assert b.guardrail_layers == 2

    def test_should_stop_true(self) -> None:
        b = AgentBehavior(stop_conditions=["missing_credentials"])
        assert b.should_stop("missing_credentials") is True

    def test_should_stop_false(self) -> None:
        b = AgentBehavior(stop_conditions=["missing_credentials"])
        assert b.should_stop("environment_change") is False

    def test_record_assumption_logs_entry(self) -> None:
        b = AgentBehavior()
        entry = b.record_assumption("Which branch?", "main")
        assert "Which branch?" in entry
        assert "main" in entry
        assert "ASSUMPTION" in entry
        assert len(b.assumption_log) == 1
        assert b.assumption_log[0] == entry

    def test_record_assumption_accumulates(self) -> None:
        b = AgentBehavior()
        b.record_assumption("Q1", "A1")
        b.record_assumption("Q2", "A2")
        assert len(b.assumption_log) == 2

    def test_should_block_on_question_false_when_assume_and_proceed(self) -> None:
        b = AgentBehavior(assume_and_proceed=True)
        assert b.should_block_on_question("anything?") is False

    def test_should_block_on_question_true_when_not_assume_and_proceed(self) -> None:
        b = AgentBehavior(assume_and_proceed=False)
        assert b.should_block_on_question("anything?") is True
