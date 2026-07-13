"""Unit tests for AgentBehavior role, goal, backstory fields (AG.6)."""

from __future__ import annotations

from general_ludd.agents.behavior import AgentBehavior


class TestAgentBehaviorRoles:
    def test_role_goal_backstory_default_to_none(self):
        b = AgentBehavior()
        assert b.role is None
        assert b.goal is None
        assert b.backstory is None

    def test_constructed_with_role_goal_backstory(self):
        b = AgentBehavior(
            role="Senior Developer",
            goal="Build reliable software",
            backstory="A veteran engineer with 15 years of experience",
        )
        assert b.role == "Senior Developer"
        assert b.goal == "Build reliable software"
        assert b.backstory == "A veteran engineer with 15 years of experience"

    def test_only_role_set_others_default(self):
        b = AgentBehavior(role="Code Reviewer")
        assert b.role == "Code Reviewer"
        assert b.goal is None
        assert b.backstory is None

    def test_model_dump_includes_role_goal_backstory(self):
        b = AgentBehavior(
            role="Tester",
            goal="Achieve 100% coverage",
            backstory="QA automation specialist",
        )
        d = b.model_dump()
        assert d["role"] == "Tester"
        assert d["goal"] == "Achieve 100% coverage"
        assert d["backstory"] == "QA automation specialist"

    def test_model_dump_with_defaults_has_none_values(self):
        b = AgentBehavior()
        d = b.model_dump()
        assert d["role"] is None
        assert d["goal"] is None
        assert d["backstory"] is None

    def test_backward_compatible_no_args_still_valid(self):
        b = AgentBehavior()
        assert b.completion_policy == "complete_all"
        assert b.self_directed_work is True
        assert b.tdd_enforced is True

    def test_serialization_roundtrip_with_roles(self):
        b = AgentBehavior(
            role="Architect",
            goal="Design scalable systems",
            backstory="Former CTO of a unicorn startup",
            max_retries=7,
        )
        d = b.to_dict()
        b2 = AgentBehavior.from_dict(d)
        assert b2.role == "Architect"
        assert b2.goal == "Design scalable systems"
        assert b2.backstory == "Former CTO of a unicorn startup"
        assert b2.max_retries == 7

    def test_default_behaviors_dont_break_with_new_fields(self):
        from general_ludd.agents.behavior import (
            default_primary_behavior,
            default_subagent_behavior,
        )

        for factory in (default_primary_behavior, default_subagent_behavior):
            b = factory()
            assert b.role is None
            assert b.goal is None
            assert b.backstory is None
