"""Deep tests for agent behavior rendering, prompt injection, tool list
generation, memory context assembly, and multi-agent coordination prompts."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
    default_primary_behavior,
    default_subagent_behavior,
)
from general_ludd.agents.context import ContextCompactor, ContextMessage
from general_ludd.agents.registry import default_registry
from general_ludd.agents.skill_context import SkillContext
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType


class DeepTestId:
    D01 = "D01"
    D02 = "D02"
    D03 = "D03"
    D04 = "D04"
    D05 = "D05"
    D06 = "D06"
    D07 = "D07"
    D08 = "D08"
    D09 = "D09"
    D10 = "D10"


# ── BehaviorTemplateRendering ──────────────────────────────────────────────


class TestBehaviorTemplateRendering:
    def test_render_all_sections_with_full_behavior(self, renderer):
        b = AgentBehavior(
            role="orchestrator",
            goal="finish the work",
            self_directed_work=True,
            tdd_enforced=True,
            commit_after_green=True,
            evidence_required=True,
            atomic_commits=True,
            session_persistence=True,
            guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True),
            allowed_command_patterns=["make *"],
            stop_conditions=["missing_credentials", "environment_change"],
            self_improve_interval=5,
            never_block_on_questions=True,
            repair_not_disable=True,
            assume_and_proceed=True,
            subagent_context_limit_lines=10,
            prefer_automated_tools=True,
        )
        result = renderer.render(b)
        assert "# Agent Behavior Configuration" in result
        assert "## Role and Goal" in result
        assert "## Task Completion" in result
        assert "## Self-Directed Work" in result
        assert "## Automated Tooling" in result
        assert "## TDD Policy" in result
        assert "## Commit-After-Green" in result
        assert "## Evidence-Based Responses" in result
        assert "## Atomic Commits" in result
        assert "## Session Persistence" in result
        assert "## Guardrail Policy" in result
        assert "## Command Policy" in result
        assert "## Stop Conditions" in result
        assert "## Self-Improvement Cycle" in result
        assert "## Never Block On Questions" in result
        assert "## Fix Means Repair, Never Disable" in result
        assert "## No-Blocking-Questions Policy" in result
        assert "## Subagent Context Limit" in result

    def test_render_minimal_behavior_omits_conditional_sections(self, renderer):
        b = AgentBehavior(
            self_directed_work=False,
            tdd_enforced=False,
            commit_after_green=False,
            evidence_required=False,
            atomic_commits=False,
            session_persistence=False,
            guardrail=GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=False),
            allowed_command_patterns=[],
            stop_conditions=[],
            self_improve_interval=0,
            never_block_on_questions=False,
            repair_not_disable=False,
            assume_and_proceed=False,
            subagent_context_limit_lines=0,
            prefer_automated_tools=False,
        )
        result = renderer.render(b)
        assert "## Self-Directed Work" not in result
        assert "## TDD Policy" not in result
        assert "## Commit-After-Green" not in result
        assert "## Evidence-Based Responses" not in result
        assert "## Atomic Commits" not in result
        assert "## Session Persistence" not in result
        assert "## Self-Improvement Cycle" not in result
        assert "## Never Block On Questions" not in result
        assert "## Fix Means Repair, Never Disable" not in result
        assert "## No-Blocking-Questions Policy" not in result
        assert "## Subagent Context Limit" not in result
        assert "## Automated Tooling" not in result

    def test_render_role_and_goal_renders_both(self, renderer):
        b = AgentBehavior(role="lead developer", goal="ship the feature")
        result = renderer.render(b)
        assert "Role: lead developer" in result
        assert "Goal: ship the feature" in result

    def test_render_role_only_no_goal(self, renderer):
        b = AgentBehavior(role="tester", goal=None)
        result = renderer.render(b)
        assert "## Role and Goal" in result
        assert "Role: tester" in result
        assert "Goal:" not in result

    def test_render_goal_only_no_role(self, renderer):
        b = AgentBehavior(role=None, goal="audit the codebase")
        result = renderer.render(b)
        assert "## Role and Goal" in result
        assert "Goal: audit the codebase" in result
        assert "Role:" not in result

    def test_render_no_role_no_goal_omits_section(self, renderer):
        b = AgentBehavior(role=None, goal=None, self_directed_work=False)
        result = renderer.render(b)
        assert "## Role and Goal" not in result

    def test_render_stop_on_blocker_policy(self, renderer):
        b = AgentBehavior(completion_policy="stop_on_blocker")
        result = renderer.render(b)
        assert "Complete work until you hit a blocker" in result

    def test_render_guardrail_enumerates_layers(self, renderer):
        b = AgentBehavior(guardrail=GuardrailConfig(config_layer=True, hook_layer=True, prompt_layer=True))
        result = renderer.render(b)
        assert "Config permission (hard gate)" in result
        assert "Runtime hook (contextual error)" in result
        assert "Agent prompt (proactive instruction)" in result

    def test_render_guardrail_single_layer(self, renderer):
        b = AgentBehavior(guardrail=GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=False))
        result = renderer.render(b)
        assert "Config permission (hard gate)" in result
        assert "Runtime hook" not in result
        assert "Agent prompt" not in result

    def test_render_stop_conditions_rendered(self, renderer):
        b = AgentBehavior(stop_conditions=["disk_full", "auth_expired", "rate_limited"])
        result = renderer.render(b)
        assert "disk_full" in result
        assert "auth_expired" in result
        assert "rate_limited" in result

    def test_render_self_improve_interval_rendered(self, renderer):
        b = AgentBehavior(self_improve_interval=10)
        result = renderer.render(b)
        assert "Every 10 ticks" in result

    def test_render_subagent_context_limit(self, renderer):
        b = AgentBehavior(subagent_context_limit_lines=5)
        result = renderer.render(b)
        assert "Return ≤5 lines" in result

    def test_render_subagent_context_limit_zero_omits(self, renderer):
        b = AgentBehavior(subagent_context_limit_lines=0)
        result = renderer.render(b)
        assert "Subagent Context Limit" not in result


# ── RenderCache ─────────────────────────────────────────────────────────────


class TestRenderCache:
    def test_render_caches_identical_behaviors(self, renderer):
        b1 = AgentBehavior(role="a")
        b2 = AgentBehavior(role="a")
        r1 = renderer.render(b1)
        r2 = renderer.render(b2)
        assert r1 is r2

    def test_render_different_behaviors_different_outputs(self, renderer):
        b1 = AgentBehavior(role="a")
        b2 = AgentBehavior(role="b")
        r1 = renderer.render(b1)
        r2 = renderer.render(b2)
        assert r1 is not r2

    def test_assumption_log_does_not_break_cache(self):
        renderer = BehaviorRenderer()
        b = AgentBehavior()
        r1 = renderer.render(b)
        b.record_assumption("q?", "a")
        r2 = renderer.render(b)
        assert r1 is r2


# ── PromptInjection ─────────────────────────────────────────────────────────


class TestPromptInjection:
    def test_render_as_prompt_includes_agent_name_and_task(self, renderer):
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="explore", task="search for bugs")
        assert "**explore**" in result
        assert "search for bugs" in result

    def test_render_as_prompt_includes_rendered_base(self, renderer):
        b = AgentBehavior(tdd_enforced=True)
        result = renderer.render_as_prompt(b, agent_name="build", task="fix tests")
        assert "## TDD Policy" in result

    def test_prompt_enhancer_allows_transform(self):
        enhancer = MagicMock()
        enhancer.enhance_prompt.return_value = "TRANSFORMED_PROMPT"
        renderer = BehaviorRenderer(prompt_enhancer=enhancer)
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="x", task="y")
        assert result == "TRANSFORMED_PROMPT"
        enhancer.enhance_prompt.assert_called_once()

    def test_prompt_enhancer_missing_method_falls_through(self):
        enhancer = object()
        renderer = BehaviorRenderer(prompt_enhancer=enhancer)
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="x", task="y")
        assert "**x**" in result
        assert "y" in result

    def test_skill_context_provider_injects_context(self):
        provider = MagicMock()
        provider.provide.return_value = SkillContext(
            skills_used=["python-expert"],
            context_text="# Python Expert\nDo Python well.",
            token_savings=42,
        )
        renderer = BehaviorRenderer(skill_context_provider=provider)
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="build", task="write python code")
        assert "# Available Skill Context" in result
        assert "Python Expert" in result
        assert "Do Python well." in result
        assert "~42 tokens" in result

    def test_skill_context_provider_empty_context_not_injected(self):
        provider = MagicMock()
        provider.provide.return_value = SkillContext(
            skills_used=[],
            context_text="",
            token_savings=0,
        )
        renderer = BehaviorRenderer(skill_context_provider=provider)
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="build", task="do nothing special")
        assert "# Available Skill Context" not in result

    def test_skill_context_provider_missing_method_falls_through(self):
        provider = object()
        renderer = BehaviorRenderer(skill_context_provider=provider)
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="build", task="any task")
        assert "# Available Skill Context" not in result

    def test_skill_context_provider_exception_falls_through(self):
        provider = MagicMock()
        provider.provide.side_effect = RuntimeError("boom")
        renderer = BehaviorRenderer(skill_context_provider=provider)
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="build", task="any task")
        assert "# Available Skill Context" not in result

    def test_both_enhancer_and_provider_wired_together(self):
        enhancer = MagicMock()
        enhancer.enhance_prompt.return_value = "FINAL"
        provider = MagicMock()
        provider.provide.return_value = SkillContext(
            skills_used=["test-quality"],
            context_text="Write good tests.",
            token_savings=10,
        )
        renderer = BehaviorRenderer(prompt_enhancer=enhancer, skill_context_provider=provider)
        b = AgentBehavior()
        result = renderer.render_as_prompt(b, agent_name="x", task="test quality")
        assert result == "FINAL"
        provider.provide.assert_called_once_with("test quality")
        enhancer.enhance_prompt.assert_called_once()


# ── ToolListGeneration ──────────────────────────────────────────────────────


class TestToolListGeneration:
    @pytest.fixture(autouse=True)
    def registry(self):
        return default_registry()

    def test_list_agent_tools_returns_all_for_primary_invoker(self, registry):
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools(invoker="build")
        names = {t["name"] for t in tools}
        assert "dispatch_explore" in names
        assert "dispatch_general" in names
        assert "dispatch_research" in names

    def test_list_agent_tools_filters_by_invoker_permissions(self, registry):
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools(invoker="plan")
        names = {t["name"] for t in tools}
        assert "dispatch_explore" in names

    def test_list_agent_tools_none_invoker_returns_all(self, registry):
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools(invoker=None)
        assert len(tools) >= 4

    def test_list_agent_tools_each_has_required_fields(self, registry):
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools(invoker="build")
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "target_agent" in tool
            assert tool["type"] == "agent_dispatch"

    def test_get_agent_as_tool_existing(self, registry):
        adapter = AgentToolAdapter(registry)
        tool = adapter.get_agent_as_tool("explore", invoker="build")
        assert tool is not None
        assert tool["name"] == "dispatch_explore"

    def test_get_agent_as_tool_missing(self, registry):
        adapter = AgentToolAdapter(registry)
        tool = adapter.get_agent_as_tool("nonexistent")
        assert tool is None

    def test_get_agent_as_tool_denied_by_invoker(self, registry):
        adapter = AgentToolAdapter(registry)
        tool = adapter.get_agent_as_tool("general", invoker="plan")
        assert tool is None


# ── MemoryContextAssembly ───────────────────────────────────────────────────


class TestMemoryContextAssembly:
    def test_context_message_creation(self):
        msg = ContextMessage(
            role="user",
            content="hello",
            token_estimate=2,
            is_system=False,
            timestamp=123456.0,
        )
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.token_estimate == 2

    def test_context_message_system_flag(self):
        msg = ContextMessage(
            role="system",
            content="you are helpful",
            is_system=True,
        )
        assert msg.is_system is True

    def test_compactor_estimate_tokens(self):
        c = ContextCompactor()
        assert c.estimate_tokens("abcdefgh") == 2
        assert c.estimate_tokens("") == 0

    def test_compactor_no_compaction_below_threshold(self):
        c = ContextCompactor(max_tokens=1000, compaction_threshold=0.8)
        msgs = [
            ContextMessage(role="user", content="hi", token_estimate=1),
            ContextMessage(role="assistant", content="hey", token_estimate=1),
        ]
        assert c.needs_compaction(msgs) is False
        assert c.compact(msgs) is msgs

    def test_compactor_triggers_above_threshold(self):
        c = ContextCompactor(max_tokens=100, compaction_threshold=0.5)
        msgs = [
            ContextMessage(role="user", content="a" * 100, token_estimate=25),
            ContextMessage(role="assistant", content="b" * 100, token_estimate=25),
            ContextMessage(role="user", content="c" * 100, token_estimate=25),
            ContextMessage(role="assistant", content="d" * 100, token_estimate=25),
            ContextMessage(role="user", content="e" * 100, token_estimate=25),
            ContextMessage(role="assistant", content="f" * 100, token_estimate=25),
        ]
        assert c.needs_compaction(msgs) is True

    def test_compactor_preserves_system_messages(self):
        c = ContextCompactor(max_tokens=100, compaction_threshold=0.5, preserve_recent_count=2)
        sys_msg = ContextMessage(role="system", content="system prompt", token_estimate=1, is_system=True)
        msgs = [
            sys_msg,
            ContextMessage(role="user", content="a" * 100, token_estimate=25),
            ContextMessage(role="user", content="b" * 100, token_estimate=25),
            ContextMessage(role="user", content="c" * 100, token_estimate=25),
            ContextMessage(role="assistant", content="recent1", token_estimate=1),
            ContextMessage(role="assistant", content="recent2", token_estimate=1),
        ]
        result = c.compact(msgs)
        assert result[0] is sys_msg
        assert any("prior context" in m.content for m in result)

    def test_compactor_preserves_recent_count(self):
        c = ContextCompactor(max_tokens=100, compaction_threshold=0.5, preserve_recent_count=2)
        msgs = [
            ContextMessage(role="user", content="old1", token_estimate=25),
            ContextMessage(role="user", content="old2", token_estimate=25),
            ContextMessage(role="user", content="old3", token_estimate=25),
            ContextMessage(role="assistant", content="r1", token_estimate=1),
            ContextMessage(role="assistant", content="r2", token_estimate=1),
        ]
        result = c.compact(msgs)
        recent_contents = [m.content for m in result if "prior context" not in m.content]
        assert "r1" in recent_contents
        assert "r2" in recent_contents

    def test_compactor_too_few_messages_no_compaction(self):
        c = ContextCompactor(max_tokens=100, compaction_threshold=0.5, preserve_recent_count=2)
        msgs = [
            ContextMessage(role="user", content="one", token_estimate=25),
            ContextMessage(role="assistant", content="two", token_estimate=25),
        ]
        result = c.compact(msgs)
        assert result is msgs

    def test_compactor_custom_summary_fn(self):
        c = ContextCompactor(max_tokens=100, compaction_threshold=0.5, preserve_recent_count=1)
        msgs = [
            ContextMessage(role="user", content="detailed question about architecture", token_estimate=25),
            ContextMessage(role="assistant", content="detailed answer about architecture", token_estimate=25),
            ContextMessage(role="user", content="recent", token_estimate=1),
        ]
        result = c.compact(msgs, summary_fn=lambda s: f"SUMMARY: {len(s)} chars")
        summary_msg = next(m for m in result if "SUMMARY" in m.content)
        assert "SUMMARY:" in summary_msg.content

    def test_compactor_ratio_zero_when_max_tokens_is_zero(self):
        c = ContextCompactor(max_tokens=0)
        msgs = [ContextMessage(role="user", content="hi", token_estimate=1000)]
        assert c.get_compaction_ratio(msgs) == 0.0

    def test_compactor_empty_messages(self):
        c = ContextCompactor()
        result = c.compact([])
        assert result == []


# ── MultiAgentCoordinationPrompts ───────────────────────────────────────────


class TestMultiAgentCoordinationPrompts:
    def test_default_registry_has_primary_and_subagents(self):
        reg = default_registry()
        agents = reg.list_agents()
        names = {a.name for a in agents}
        assert "build" in names
        assert "plan" in names
        assert "explore" in names
        assert "general" in names
        assert "research" in names

    def test_default_registry_can_invoke_build_dispatching(self):
        reg = default_registry()
        assert reg.can_invoke("build", "explore") is True
        assert reg.can_invoke("build", "general") is True
        assert reg.can_invoke("build", "research") is True
        assert reg.can_invoke("plan", "explore") is True

    def test_default_registry_cannot_dispatch_without_permission(self):
        reg = default_registry()
        assert reg.can_invoke("explore", "general") is False

    def test_default_registry_nonexistent_agents(self):
        reg = default_registry()
        assert reg.can_invoke("nobody", "explore") is False
        assert reg.can_invoke("build", "nobody") is False

    def test_default_registry_get_returns_config(self):
        reg = default_registry()
        config = reg.get("build")
        assert config is not None
        assert config.name == "build"
        assert config.type == AgentType.PRIMARY

    def test_default_registry_get_missing_returns_none(self):
        reg = default_registry()
        assert reg.get("nobody") is None

    def test_default_registry_list_subagents(self):
        reg = default_registry()
        subs = reg.list_subagents()
        names = {s.name for s in subs}
        assert "explore" in names
        assert "general" in names
        assert "research" in names
        for s in subs:
            assert s.type == AgentType.SUBAGENT

    def test_default_registry_get_behavior_primary(self):
        reg = default_registry()
        b = reg.get_behavior("build")
        assert b.role == "primary orchestrator"
        assert b.self_directed_work is True

    def test_default_registry_get_behavior_subagent(self):
        reg = default_registry()
        b = reg.get_behavior("explore")
        assert b.role == "specialized subagent"
        assert b.self_directed_work is False

    def test_render_behavior_prompt_renders_for_known_agent(self):
        reg = default_registry()
        prompt = reg.render_behavior_prompt("build", "fix all bugs")
        assert prompt is not None
        assert "build" in prompt
        assert "fix all bugs" in prompt

    def test_render_behavior_prompt_unknown_agent_returns_none(self):
        reg = default_registry()
        prompt = reg.render_behavior_prompt("nobody", "do work")
        assert prompt is None

    def test_agent_config_dataclass_shape(self):
        cfg = AgentConfig(
            name="test-agent",
            description="A test agent",
            type=AgentType.SUBAGENT,
            permissions=AgentPermission(can_read=True, can_edit=False),
            max_concurrent=2,
        )
        assert cfg.name == "test-agent"
        assert cfg.type == AgentType.SUBAGENT
        assert cfg.permissions.can_read is True
        assert cfg.permissions.can_edit is False
        assert cfg.max_concurrent == 2

    def test_agent_task_dataclass_shape(self):
        task = AgentTask(
            task_id="T001",
            agent_name="build",
            description="Test task",
            prompt="Do the thing",
            depth=1,
            estimated_effort="large",
        )
        assert task.task_id == "T001"
        assert task.depth == 1
        assert task.estimated_effort == "large"

    def test_primary_and_subagent_have_different_behaviors(self):
        p = default_primary_behavior()
        s = default_subagent_behavior()
        assert p.self_directed_work is True
        assert s.self_directed_work is False
        assert p.role != s.role

    def test_registry_seal_prevents_new_registration(self):
        reg = default_registry()
        with pytest.raises(RuntimeError, match="sealed"):
            reg.register(
                AgentConfig(
                    name="newbie",
                    description="new",
                    type=AgentType.SUBAGENT,
                    permissions=AgentPermission(),
                )
            )

    def test_plan_cannot_dispatch_general(self):
        reg = default_registry()
        assert reg.can_invoke("plan", "general") is False

    def test_build_can_dispatch_explore(self):
        reg = default_registry()
        assert reg.can_invoke("build", "explore") is True

    def test_wildcard_subagent_allows_any_registered_subagent(self):
        reg = default_registry()
        for name in ("explore", "general", "research"):
            assert reg.can_invoke("build", name) is True

    def test_wildcard_does_not_allow_nonexistent_agent(self):
        reg = default_registry()
        assert reg.can_invoke("build", "anything-at-all") is False


# ── AssumptionsAndQuestionBlocking ──────────────────────────────────────────


class TestAssumptionsAndQuestionBlocking:
    def test_record_assumption_appends_and_returns(self):
        b = AgentBehavior()
        result = b.record_assumption("Which port?", "assumed 8080")
        assert "ASSUMPTION:" in result
        assert "Which port?" in result
        assert "assumed 8080" in result
        assert len(b.assumption_log) == 1

    def test_record_multiple_assumptions(self):
        b = AgentBehavior()
        b.record_assumption("Q1", "A1")
        b.record_assumption("Q2", "A2")
        assert len(b.assumption_log) == 2

    def test_should_block_when_assume_and_proceed_is_false(self):
        b = AgentBehavior(assume_and_proceed=False)
        assert b.should_block_on_question("anything") is True

    def test_should_not_block_when_assume_and_proceed_is_true(self):
        b = AgentBehavior(assume_and_proceed=True)
        assert b.should_block_on_question("anything") is False

    def test_assume_and_proceed_default_is_true(self):
        b = AgentBehavior()
        assert b.assume_and_proceed is True


# ── ValidationEdgeCases ─────────────────────────────────────────────────────


class TestValidationEdgeCases:
    def test_max_retries_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            AgentBehavior(max_retries=-1)

    def test_self_improve_interval_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            AgentBehavior(self_improve_interval=-1)

    def test_guardrail_zero_layers_raises(self):
        with pytest.raises(ValueError, match=r"(?i)at least one"):
            GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False)

    def test_can_create_with_zero_max_retries(self):
        b = AgentBehavior(max_retries=0)
        assert b.max_retries == 0

    def test_can_create_with_zero_self_improve_interval(self):
        b = AgentBehavior(self_improve_interval=0)
        assert b.self_improve_interval == 0

    def test_prefer_automated_tools_default(self):
        b = AgentBehavior()
        assert b.prefer_automated_tools is True

    def test_research_before_build_default(self):
        b = AgentBehavior()
        assert b.research_before_build is True

    def test_subagent_context_limit_default(self):
        b = AgentBehavior()
        assert b.subagent_context_limit_lines == 10

    def test_no_role_no_goal_no_self_directed_no_auto_minimal_render(self, renderer):
        b = AgentBehavior(
            role=None,
            goal=None,
            self_directed_work=False,
            prefer_automated_tools=False,
            tdd_enforced=False,
            commit_after_green=False,
            evidence_required=False,
            atomic_commits=False,
            session_persistence=False,
            allowed_command_patterns=[],
            stop_conditions=[],
            self_improve_interval=0,
            never_block_on_questions=False,
            repair_not_disable=False,
            assume_and_proceed=False,
            subagent_context_limit_lines=0,
        )
        result = renderer.render(b)
        lines = result.split("\n")
        assert len([line for line in lines if line.startswith("## ")]) <= 3


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def renderer():
    return BehaviorRenderer()
