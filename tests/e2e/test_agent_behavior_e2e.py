"""E2E tests for agent behavior codification pipeline."""

from __future__ import annotations

import pytest

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
    default_primary_behavior,
    default_subagent_behavior,
)
from general_ludd.agents.registry import default_registry
from general_ludd.agents.types import AgentType
from general_ludd.prompts.registry import PromptRegistry


class TestBehaviorEndToEnd:
    def test_primary_agent_gets_full_behavior_prompt(self):
        reg = default_registry()
        build = reg.get("build")
        assert build is not None

        behavior = default_primary_behavior()
        renderer = BehaviorRenderer()
        prompt = renderer.render_as_prompt(behavior, agent_name=build.name, task="implement feature")

        assert "build" in prompt
        assert "implement feature" in prompt
        assert "complete all" in prompt.lower()
        assert "TDD" in prompt
        assert "evidence" in prompt.lower()
        assert "SESSION" in prompt
        assert "guardrail" in prompt.lower()
        assert "self" in prompt.lower()

    def test_subagent_gets_restricted_behavior(self):
        behavior = default_subagent_behavior()
        renderer = BehaviorRenderer()
        prompt = renderer.render_as_prompt(behavior, agent_name="explore", task="search codebase")

        assert "explore" in prompt
        assert "TDD" in prompt
        assert "SESSION" in prompt
        assert "self-directed" not in prompt.lower() or "Self-Directed Work" not in prompt

    def test_behavior_renders_into_prompt_registry(self):
        behavior = default_primary_behavior()
        renderer = BehaviorRenderer()
        rendered = renderer.render(behavior)

        prompt_reg = PromptRegistry()
        prompt_reg.register("agent_behavior", rendered)
        assert "agent_behavior" in prompt_reg.list_templates()

    def test_custom_behavior_per_agent(self):
        custom_behavior = AgentBehavior(
            completion_policy="stop_on_blocker",
            self_directed_work=False,
            tdd_enforced=False,
            evidence_required=True,
            allowed_command_patterns=["make *", "npm *"],
        )
        renderer = BehaviorRenderer()
        prompt = renderer.render(custom_behavior)

        assert "blocker" in prompt.lower()
        assert "TDD" not in prompt
        assert "npm" in prompt
        assert "evidence" in prompt.lower()

    def test_guardrail_validation_in_pipeline(self):
        with pytest.raises(ValueError):
            GuardrailConfig(
                config_layer=False, hook_layer=False, prompt_layer=False
            ).ensure_valid()

    def test_behavior_serialization_roundtrip_via_registry(self):
        behavior = default_primary_behavior()
        data = behavior.to_dict()

        restored = AgentBehavior.from_dict(data)
        assert restored.completion_policy == behavior.completion_policy
        assert restored.self_directed_work == behavior.self_directed_work
        assert restored.tdd_enforced == behavior.tdd_enforced
        assert restored.session_persistence == behavior.session_persistence
        assert restored.guardrail.config_layer == behavior.guardrail.config_layer

    def test_all_default_agents_have_matching_behaviors(self):
        reg = default_registry()
        renderer = BehaviorRenderer()

        for agent in reg.list_agents():
            behavior = default_primary_behavior() if agent.type == AgentType.PRIMARY else default_subagent_behavior()

            prompt = renderer.render_as_prompt(behavior, agent_name=agent.name, task="test")
            assert agent.name in prompt
            assert len(prompt) > 100

    def test_stop_conditions_integrated_with_behavior(self):
        behavior = default_primary_behavior()
        assert behavior.should_stop("missing_credentials") is True
        assert behavior.should_stop("found_bug") is False
        assert behavior.should_stop("environment_change") is True

    def test_behavior_rendered_for_task_dispatch(self):
        behavior = default_primary_behavior()
        renderer = BehaviorRenderer()
        task_description = "Fix the authentication bug in the login module"
        agent_name = "build"

        system_prompt = renderer.render_as_prompt(behavior, agent_name=agent_name, task=task_description)

        assert "# Agent Behavior" in system_prompt or "Agent Behavior" in system_prompt
        assert agent_name in system_prompt
        assert task_description in system_prompt
        assert "complete all" in system_prompt.lower()
