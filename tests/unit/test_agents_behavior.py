"""Unit tests for agents/behavior.py — AgentBehavior, GuardrailConfig, BehaviorRenderer."""

from __future__ import annotations

import pytest

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
    default_primary_behavior,
    default_subagent_behavior,
)


class TestGuardrailConfig:
    def test_defaults_all_layers_enabled(self):
        gc = GuardrailConfig()
        assert gc.config_layer is True
        assert gc.hook_layer is True
        assert gc.prompt_layer is True

    def test_layer_count_all(self):
        gc = GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True)
        assert gc.layer_count() == 3

    def test_layer_count_some(self):
        gc = GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=False)
        assert gc.layer_count() == 1

    def test_layer_count_zero_raises(self):
        with pytest.raises(ValueError, match="At least one guardrail layer"):
            GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False)

    def test_post_init_validates(self):
        gc = GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=False)
        assert gc.layer_count() == 1


class TestAgentBehaviorDefaults:
    def test_defaults(self):
        ab = AgentBehavior()
        assert ab.completion_policy == "complete_all"
        assert ab.self_directed_work is True
        assert ab.tdd_enforced is True
        assert ab.commit_after_green is True
        assert ab.evidence_required is True
        assert ab.atomic_commits is True
        assert ab.session_persistence is True
        assert ab.max_retries == 3
        assert ab.self_improve_interval == 0
        assert ab.never_block_on_questions is True
        assert ab.repair_not_disable is True
        assert ab.allowed_command_patterns == ["make *"]
        assert ab.stop_conditions == ["missing_credentials", "environment_change"]
        assert ab.assume_and_proceed is True
        assert ab.subagent_context_limit_lines == 10

    def test_max_retries_non_negative(self):
        ab = AgentBehavior(max_retries=5)
        assert ab.max_retries == 5

    def test_max_retries_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            AgentBehavior(max_retries=-1)

    def test_self_improve_interval_non_negative(self):
        ab = AgentBehavior(self_improve_interval=10)
        assert ab.self_improve_interval == 10

    def test_self_improve_interval_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            AgentBehavior(self_improve_interval=-5)


class TestAgentBehaviorShouldStop:
    def test_known_condition(self):
        ab = AgentBehavior(stop_conditions=["missing_credentials"])
        assert ab.should_stop("missing_credentials") is True

    def test_unknown_condition(self):
        ab = AgentBehavior(stop_conditions=["missing_credentials"])
        assert ab.should_stop("network_error") is False


class TestAgentBehaviorIsCommandAllowed:
    def test_make_any(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("make test") is True

    def test_non_matching_pattern(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("git push") is False

    def test_empty_command(self):
        ab = AgentBehavior()
        assert ab.is_command_allowed("") is False

    def test_whitespace_command(self):
        ab = AgentBehavior()
        assert ab.is_command_allowed("   ") is False

    def test_metacharacters_blocked(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("make test; rm -rf /") is False

    def test_pipe_blocked(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("make test | grep fail") is False

    def test_ampersand_blocked(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("make test && make lint") is False

    def test_dollar_substitution_blocked(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("make $(echo test)") is False

    def test_leading_dash_blocked(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("-flag") is False

    def test_env_prefix_blocked(self):
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        assert ab.is_command_allowed("FOO=bar make test") is False

    def test_exact_pattern_match(self):
        ab = AgentBehavior(allowed_command_patterns=["make test"])
        assert ab.is_command_allowed("make test") is True
        assert ab.is_command_allowed("make lint") is False

    def test_wildcard_match(self):
        ab = AgentBehavior(allowed_command_patterns=["make test-*"])
        assert ab.is_command_allowed("make test-unit") is True
        assert ab.is_command_allowed("make lint") is False


class TestAgentBehaviorRecordAssumption:
    def test_records_and_returns(self):
        ab = AgentBehavior()
        result = ab.record_assumption("Which DB?", "PostgreSQL")
        assert "ASSUMPTION" in result
        assert "PostgreSQL" in result
        assert len(ab.assumption_log) == 1

    def test_accumulates(self):
        ab = AgentBehavior()
        ab.record_assumption("q1", "a1")
        ab.record_assumption("q2", "a2")
        assert len(ab.assumption_log) == 2


class TestAgentBehaviorShouldBlockOnQuestion:
    def test_assume_and_proceed_true(self):
        ab = AgentBehavior(assume_and_proceed=True)
        assert ab.should_block_on_question("q") is False

    def test_assume_and_proceed_false(self):
        ab = AgentBehavior(assume_and_proceed=False)
        assert ab.should_block_on_question("q") is True


class TestAgentBehaviorGuardrailLayers:
    def test_delegates_to_guardrail(self):
        ab = AgentBehavior(guardrail=GuardrailConfig(
            config_layer=True, hook_layer=True, prompt_layer=False
        ))
        assert ab.guardrail_layers == 2


class TestAgentBehaviorToFromDict:
    def test_to_dict_roundtrip(self):
        ab = AgentBehavior(
            role="test_role",
            goal="test goal",
            max_retries=5,
            allowed_command_patterns=["make *", "ls"],
        )
        d = ab.to_dict()
        restored = AgentBehavior.from_dict(d)
        assert restored.role == "test_role"
        assert restored.max_retries == 5
        assert restored.allowed_command_patterns == ["make *", "ls"]


class TestBehaviorRenderer:
    def test_render_basic(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior()
        result = renderer.render(ab)
        assert "# Agent Behavior Configuration" in result
        assert "## Task Completion" in result

    def test_render_includes_tdd_when_enabled(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(tdd_enforced=True)
        result = renderer.render(ab)
        assert "## TDD Policy" in result

    def test_render_excludes_tdd_when_disabled(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(tdd_enforced=False)
        result = renderer.render(ab)
        assert "## TDD Policy" not in result

    def test_render_includes_self_improvement_when_interval_positive(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(self_improve_interval=10)
        result = renderer.render(ab)
        assert "## Self-Improvement Cycle" in result

    def test_render_excludes_self_improvement_when_zero(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(self_improve_interval=0)
        result = renderer.render(ab)
        assert "## Self-Improvement Cycle" not in result

    def test_render_guardrail_layers(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(guardrail=GuardrailConfig(
            config_layer=True, hook_layer=True, prompt_layer=True
        ))
        result = renderer.render(ab)
        assert "## Guardrail Policy" in result
        assert "Config permission" in result

    def test_render_no_guardrail_when_zero_layers(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(guardrail=GuardrailConfig(
            config_layer=True, hook_layer=False, prompt_layer=False
        ))
        result = renderer.render(ab)
        assert "## Guardrail Policy" in result

    def test_render_command_policy(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(allowed_command_patterns=["make *"])
        result = renderer.render(ab)
        assert "## Command Policy" in result
        assert "make *" in result

    def test_render_stop_conditions(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(stop_conditions=["missing_credentials", "environment_change"])
        result = renderer.render(ab)
        assert "## Stop Conditions" in result
        assert "missing_credentials" in result

    def test_render_never_block_on_questions(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(never_block_on_questions=True)
        result = renderer.render(ab)
        assert "## Never Block On Questions" in result

    def test_render_repair_not_disable(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(repair_not_disable=True)
        result = renderer.render(ab)
        assert "## Fix Means Repair, Never Disable" in result

    def test_render_no_blocking_questions_when_assume_and_proceed(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(assume_and_proceed=True)
        result = renderer.render(ab)
        assert "## No-Blocking-Questions Policy" in result

    def test_render_subagent_context_limit(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(subagent_context_limit_lines=10)
        result = renderer.render(ab)
        assert "## Subagent Context Limit" in result

    def test_render_no_subagent_limit_when_zero(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior(subagent_context_limit_lines=0)
        result = renderer.render(ab)
        assert "## Subagent Context Limit" not in result

    def test_render_caches_result(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior()
        first = renderer.render(ab)
        second = renderer.render(ab)
        assert first is second

    def test_render_as_prompt(self):
        renderer = BehaviorRenderer()
        ab = AgentBehavior()
        result = renderer.render_as_prompt(ab, "TestAgent", "do stuff")
        assert "TestAgent" in result
        assert "do stuff" in result
        assert "# Agent Behavior Configuration" in result

    def test_cache_key_excludes_assumption_log(self):
        ab1 = AgentBehavior()
        ab2 = AgentBehavior()
        ab2.record_assumption("q", "a")
        key1 = BehaviorRenderer._cache_key(ab1)
        key2 = BehaviorRenderer._cache_key(ab2)
        assert key1 == key2

    def test_cache_key_different_behaviors_different(self):
        ab1 = AgentBehavior(role="a")
        ab2 = AgentBehavior(role="b")
        key1 = BehaviorRenderer._cache_key(ab1)
        key2 = BehaviorRenderer._cache_key(ab2)
        assert key1 != key2


class TestDefaultBehaviors:
    def test_default_primary_behavior(self):
        ab = default_primary_behavior()
        assert ab.self_directed_work is True
        assert ab.completion_policy == "complete_all"

    def test_default_subagent_behavior(self):
        ab = default_subagent_behavior()
        assert ab.self_directed_work is False
        assert ab.completion_policy == "complete_all"
