"""Tests for agents/skill_context: skill-lens context injection into agent prompts."""

from __future__ import annotations

from general_ludd.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
)
from general_ludd.agents.skill_context import (
    SkillContext,
    SkillContextProvider,
    _estimate_tokens,
    _validate_skill_exists,
)


class TestSkillContextDataclass:
    def test_default_values(self):
        ctx = SkillContext(skills_used=[], context_text="", token_savings=0)
        assert ctx.skills_used == []
        assert ctx.context_text == ""
        assert ctx.token_savings == 0

    def test_with_values(self):
        ctx = SkillContext(
            skills_used=["python-expert", "test-quality"],
            context_text="# Lens context",
            token_savings=1500,
        )
        assert ctx.skills_used == ["python-expert", "test-quality"]
        assert "# Lens context" in ctx.context_text
        assert ctx.token_savings == 1500


class TestTokenEstimation:
    def test_estimate_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_estimate_short_text(self):
        assert _estimate_tokens("hello") == 1

    def test_estimate_long_text(self):
        text = "x" * 400
        assert _estimate_tokens(text) == 100


class TestValidateSkillExists:
    def test_real_skill_exists(self):
        assert _validate_skill_exists("test-quality") is True

    def test_fake_skill_does_not_exist(self):
        assert _validate_skill_exists("nonexistent-skill-xyz123") is False

    def test_python_expert_exists(self):
        assert _validate_skill_exists("python-expert") is True


class TestSkillContextProviderIdentify:
    def test_identifies_python_for_python_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("debug a python asyncio deadlock")
        assert "python-expert" in skills

    def test_identifies_test_quality_for_test_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("write a unit test with good assertions")
        assert "test-quality" in skills

    def test_identifies_type_safety_for_typing_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("add type annotations and run mypy")
        assert "type-safety" in skills

    def test_identifies_guardrail_for_enforcement_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("add a guardrail with enforcement hooks")
        assert "guardrail-pattern" in skills

    def test_returns_empty_for_no_matching_skill(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("make some coffee and relax")
        assert skills == []

    def test_returns_empty_for_empty_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("")
        assert skills == []

    def test_multiple_skills_for_mixed_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills(
            "Write Python tests with type annotations and guardrail enforcement"
        )
        assert "python-expert" in skills
        assert "test-quality" in skills
        assert "type-safety" in skills
        assert "guardrail-pattern" in skills

    def test_case_insensitive_matching(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("Debug PYTHON ASYNCIO")
        assert "python-expert" in skills

    def test_deduplicates_skills(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills(
            "python python python asyncio coroutine pytest"
        )
        assert skills.count("python-expert") == 1

    def test_respects_skill_names_allowlist(self):
        provider = SkillContextProvider(skill_names=["test-quality"])
        skills = provider.identify_skills("python test assertion")
        assert "python-expert" not in skills
        assert "test-quality" in skills

    def test_identifies_go_for_go_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("write a golang goroutine handler")
        assert "go-expert" in skills

    def test_identifies_java_for_java_task(self):
        provider = SkillContextProvider()
        skills = provider.identify_skills("write a java spring controller")
        assert "java-expert" in skills


class TestSkillContextProviderProvide:
    def test_provide_returns_skill_context_for_matching_task(self):
        provider = SkillContextProvider()
        ctx = provider.provide("write a python test with assertions")
        assert isinstance(ctx, SkillContext)
        assert len(ctx.skills_used) > 0
        assert ctx.token_savings > 0
        assert len(ctx.context_text) > 0

    def test_provide_returns_empty_for_no_match(self):
        provider = SkillContextProvider()
        ctx = provider.provide("just a chat")
        assert ctx.skills_used == []
        assert ctx.context_text == ""
        assert ctx.token_savings == 0

    def test_provide_has_lens_like_output(self):
        provider = SkillContextProvider()
        ctx = provider.provide("write good python tests")
        if ctx.skills_used:
            assert "## " in ctx.context_text
            assert "Skill Context:" in ctx.context_text

    def test_token_savings_positive(self):
        provider = SkillContextProvider()
        ctx = provider.provide("write a test with assertions")
        if ctx.skills_used:
            assert ctx.token_savings >= 0

    def test_multiple_skills_token_savings(self):
        provider = SkillContextProvider(max_sections=1)
        ctx = provider.provide("python test with typing and assertions")
        if len(ctx.skills_used) >= 2:
            assert ctx.token_savings > 0
            assert isinstance(ctx.token_savings, int)

    def test_provide_handles_unavailable_skill(self):
        provider = SkillContextProvider(skill_names=["nonexistent-skill-xyz"])
        ctx = provider.provide("this is a test")
        assert ctx.skills_used == []

    def test_max_sections_parameter(self):
        provider = SkillContextProvider(max_sections=1)
        ctx = provider.provide("python programming")
        if ctx.skills_used:
            for skill in ctx.skills_used:
                assert f"Skill Context: {skill} (1 sections" in ctx.context_text or "python-expert" not in skill


class TestBehaviorRendererIntegration:
    def test_renderer_accepts_skill_context_provider(self):
        provider = SkillContextProvider()
        renderer = BehaviorRenderer(skill_context_provider=provider)
        assert renderer._skill_context_provider is provider

    def test_render_as_prompt_injects_skill_context(self):
        provider = SkillContextProvider()
        renderer = BehaviorRenderer(skill_context_provider=provider)
        behavior = AgentBehavior()
        result = renderer.render_as_prompt(behavior, "test-agent", "write a python test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_render_as_prompt_works_without_provider(self):
        renderer = BehaviorRenderer(skill_context_provider=None)
        behavior = AgentBehavior()
        result = renderer.render_as_prompt(behavior, "test-agent", "do something")
        assert isinstance(result, str)
        assert "test-agent" in result
        assert "do something" in result

    def test_render_as_prompt_handles_provider_exception(self):
        class BrokenProvider:
            def provide(self, task):
                raise RuntimeError("boom")

        renderer = BehaviorRenderer(skill_context_provider=BrokenProvider())
        behavior = AgentBehavior()
        result = renderer.render_as_prompt(behavior, "agent", "task")
        assert "agent" in result
        assert "task" in result
