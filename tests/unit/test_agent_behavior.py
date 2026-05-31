"""Unit tests for AgentBehavior configuration and BehaviorRenderer."""

from __future__ import annotations

import pytest

from agentic_harness.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
)


class TestAgentBehaviorDefaults:
    def test_default_behavior_has_all_policies_enabled(self):
        b = AgentBehavior()
        assert b.completion_policy == "complete_all"
        assert b.self_directed_work is True
        assert b.tdd_enforced is True
        assert b.commit_after_green is True
        assert b.evidence_required is True
        assert b.atomic_commits is True
        assert b.session_persistence is True
        assert b.max_retries == 3
        assert b.allowed_command_patterns == ["make *"]
        assert b.guardrail_layers >= 1

    def test_default_guardrail_config(self):
        b = AgentBehavior()
        assert b.guardrail is not None
        assert b.guardrail.config_layer is True
        assert b.guardrail.hook_layer is True
        assert b.guardrail.prompt_layer is True

    def test_guardrail_layers_count(self):
        b = AgentBehavior()
        assert b.guardrail_layers == 3

    def test_guardrail_layers_with_some_disabled(self):
        b = AgentBehavior(
            guardrail=GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=True)
        )
        assert b.guardrail_layers == 2


class TestAgentBehaviorCustomization:
    def test_custom_completion_policy(self):
        b = AgentBehavior(completion_policy="stop_on_blocker")
        assert b.completion_policy == "stop_on_blocker"

    def test_custom_allowed_commands(self):
        b = AgentBehavior(allowed_command_patterns=["make *", "pytest *"])
        assert "pytest *" in b.allowed_command_patterns

    def test_disable_self_directed(self):
        b = AgentBehavior(self_directed_work=False)
        assert b.self_directed_work is False

    def test_custom_max_retries(self):
        b = AgentBehavior(max_retries=5)
        assert b.max_retries == 5

    def test_stop_conditions(self):
        b = AgentBehavior(stop_conditions=["missing_credentials", "payment_required"])
        assert "missing_credentials" in b.stop_conditions


class TestAgentBehaviorStopConditions:
    def test_default_stop_conditions(self):
        b = AgentBehavior()
        assert "missing_credentials" in b.stop_conditions
        assert "environment_change" in b.stop_conditions

    def test_should_stop_on_known_condition(self):
        b = AgentBehavior()
        assert b.should_stop("missing_credentials") is True

    def test_should_not_stop_on_unknown_condition(self):
        b = AgentBehavior()
        assert b.should_stop("tired") is False

    def test_should_stop_respects_self_directed(self):
        b = AgentBehavior(self_directed_work=True)
        assert b.should_stop("found_bug") is False

    def test_should_stop_on_unrecoverable(self):
        b = AgentBehavior(self_directed_work=True)
        assert b.should_stop("missing_credentials") is True


class TestAgentBehaviorSerialization:
    def test_to_dict_roundtrip(self):
        b = AgentBehavior(
            completion_policy="complete_all",
            self_directed_work=True,
            tdd_enforced=True,
            max_retries=5,
        )
        d = b.to_dict()
        b2 = AgentBehavior.from_dict(d)
        assert b2.completion_policy == b.completion_policy
        assert b2.self_directed_work == b.self_directed_work
        assert b2.tdd_enforced == b.tdd_enforced
        assert b2.max_retries == b.max_retries

    def test_to_dict_includes_all_fields(self):
        b = AgentBehavior()
        d = b.to_dict()
        assert "completion_policy" in d
        assert "self_directed_work" in d
        assert "tdd_enforced" in d
        assert "commit_after_green" in d
        assert "evidence_required" in d
        assert "atomic_commits" in d
        assert "session_persistence" in d
        assert "guardrail" in d
        assert "allowed_command_patterns" in d
        assert "stop_conditions" in d
        assert "max_retries" in d


class TestGuardrailConfig:
    def test_all_layers_default_true(self):
        g = GuardrailConfig()
        assert g.config_layer is True
        assert g.hook_layer is True
        assert g.prompt_layer is True

    def test_partial_layers(self):
        g = GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=True)
        assert g.layer_count() == 2

    def test_validate_raises_on_no_layers(self):
        g = GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False)
        with pytest.raises(ValueError, match=r"(?i)at least one"):
            g.validate()


class TestBehaviorRenderer:
    def test_render_produces_non_empty_string(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior()
        result = renderer.render(b)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_render_includes_completion_policy(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(completion_policy="complete_all")
        result = renderer.render(b)
        assert "complete all" in result.lower()

    def test_render_includes_tdd_section_when_enabled(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(tdd_enforced=True)
        result = renderer.render(b)
        assert "TDD" in result or "test" in result.lower()

    def test_render_includes_evidence_section_when_enabled(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(evidence_required=True)
        result = renderer.render(b)
        assert "evidence" in result.lower()

    def test_render_includes_guardrail_section(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior()
        result = renderer.render(b)
        assert "guardrail" in result.lower() or "layer" in result.lower()

    def test_render_includes_session_persistence_when_enabled(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(session_persistence=True)
        result = renderer.render(b)
        assert "session" in result.lower() or "SESSION" in result

    def test_render_excludes_section_when_disabled(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(tdd_enforced=False)
        result = renderer.render(b)
        assert "TDD" not in result

    def test_render_includes_command_policy(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(allowed_command_patterns=["make *"])
        result = renderer.render(b)
        assert "make" in result

    def test_render_includes_commit_after_green(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(commit_after_green=True)
        result = renderer.render(b)
        assert "commit" in result.lower()

    def test_render_includes_self_directed_work(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior(self_directed_work=True)
        result = renderer.render(b)
        assert "self" in result.lower() or "gap" in result.lower() or "fix" in result.lower()


class TestBehaviorRendererMarkdown:
    def test_render_starts_with_header(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior()
        result = renderer.render(b)
        assert result.startswith("#")

    def test_render_has_sections(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior()
        result = renderer.render(b)
        assert "##" in result

    def test_render_as_template_context(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior()
        renderer.render(b)
        prompt = renderer.render_as_prompt(b, agent_name="build", task="fix bug")
        assert "build" in prompt
        assert "fix bug" in prompt


class TestDefaultBehaviors:
    def test_default_primary_behavior(self):
        from agentic_harness.agents.behavior import default_primary_behavior

        b = default_primary_behavior()
        assert b.completion_policy == "complete_all"
        assert b.self_directed_work is True
        assert b.tdd_enforced is True
        assert b.allowed_command_patterns == ["make *"]

    def test_default_subagent_behavior(self):
        from agentic_harness.agents.behavior import default_subagent_behavior

        b = default_subagent_behavior()
        assert b.completion_policy == "complete_all"
        assert b.self_directed_work is False
        assert b.allowed_command_patterns == ["make *"]
