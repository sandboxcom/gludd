"""Deep behavioral tests for agents/registry.py — AgentRegistry and default_registry()."""

from __future__ import annotations

import pytest

from general_ludd.agents.registry import AgentRegistry, default_registry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType


class TestAgentRegistryRegisterAndGet:
    def test_register_and_get(self):
        r = AgentRegistry()
        cfg = AgentConfig(name="test", description="desc", type=AgentType.SUBAGENT)
        r.register(cfg)
        assert r.get("test") is cfg

    def test_get_missing_returns_none(self):
        r = AgentRegistry()
        assert r.get("nonexistent") is None

    def test_register_overwrites_existing(self):
        r = AgentRegistry()
        cfg1 = AgentConfig(name="a", description="first", type=AgentType.SUBAGENT)
        cfg2 = AgentConfig(name="a", description="second", type=AgentType.PRIMARY)
        r.register(cfg1)
        r.register(cfg2)
        result = r.get("a")
        assert result is not None
        assert result.description == "second"

    def test_list_agents_empty(self):
        r = AgentRegistry()
        assert r.list_agents() == []

    def test_list_agents_returns_all(self):
        r = AgentRegistry()
        r.register(AgentConfig(name="a", description="", type=AgentType.SUBAGENT))
        r.register(AgentConfig(name="b", description="", type=AgentType.PRIMARY))
        assert len(r.list_agents()) == 2


class TestAgentRegistryListSubagents:
    def test_filters_subagents_only(self):
        r = AgentRegistry()
        r.register(AgentConfig(name="primary", description="", type=AgentType.PRIMARY))
        r.register(AgentConfig(name="sub1", description="", type=AgentType.SUBAGENT))
        r.register(AgentConfig(name="sub2", description="", type=AgentType.SUBAGENT))
        subs = r.list_subagents()
        assert len(subs) == 2
        assert all(a.type == AgentType.SUBAGENT for a in subs)

    def test_no_subagents_returns_empty(self):
        r = AgentRegistry()
        r.register(AgentConfig(name="p", description="", type=AgentType.PRIMARY))
        assert r.list_subagents() == []


class TestAgentRegistryCanInvoke:
    def _make_agent(self, name, can_dispatch, allowed, agent_type=AgentType.SUBAGENT):
        return AgentConfig(
            name=name,
            description="",
            type=agent_type,
            permissions=AgentPermission(
                can_dispatch_subagents=can_dispatch,
                allowed_subagents=allowed,
            ),
        )

    def test_can_invoke_basic(self):
        r = AgentRegistry()
        r.register(self._make_agent("invoker", True, ["target"]))
        r.register(self._make_agent("target", False, []))
        assert r.can_invoke("invoker", "target") is True

    def test_cannot_invoke_without_dispatch_permission(self):
        r = AgentRegistry()
        r.register(self._make_agent("invoker", False, ["target"]))
        r.register(self._make_agent("target", False, []))
        assert r.can_invoke("invoker", "target") is False

    def test_cannot_invoke_not_in_allowed_list(self):
        r = AgentRegistry()
        r.register(self._make_agent("invoker", True, ["other"]))
        r.register(self._make_agent("target", False, []))
        assert r.can_invoke("invoker", "target") is False

    def test_invoker_missing(self):
        r = AgentRegistry()
        r.register(self._make_agent("target", False, []))
        assert r.can_invoke("nonexistent", "target") is False

    def test_target_missing(self):
        r = AgentRegistry()
        r.register(self._make_agent("invoker", True, ["target"]))
        assert r.can_invoke("invoker", "nonexistent") is False

    def test_wildcard_matches_any(self):
        r = AgentRegistry()
        r.register(self._make_agent("invoker", True, ["*"]))
        r.register(self._make_agent("anything", False, []))
        assert r.can_invoke("invoker", "anything") is True

    def test_fnmatch_glob_pattern(self):
        r = AgentRegistry()
        r.register(self._make_agent("invoker", True, ["sub-*"]))
        r.register(self._make_agent("sub-foo", False, []))
        r.register(self._make_agent("sub-bar", False, []))
        r.register(self._make_agent("other", False, []))
        assert r.can_invoke("invoker", "sub-foo") is True
        assert r.can_invoke("invoker", "sub-bar") is True
        assert r.can_invoke("invoker", "other") is False

    def test_fnmatch_question_mark(self):
        r = AgentRegistry()
        r.register(self._make_agent("invoker", True, ["agent-?"]))
        r.register(self._make_agent("agent-1", False, []))
        r.register(self._make_agent("agent-12", False, []))
        assert r.can_invoke("invoker", "agent-1") is True
        assert r.can_invoke("invoker", "agent-12") is False

    def test_invoker_can_dispatch_false_blocks(self):
        r = AgentRegistry()
        cfg = AgentConfig(
            name="invoker",
            description="",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(can_dispatch_subagents=False, allowed_subagents=["target"]),
        )
        r.register(cfg)
        r.register(self._make_agent("target", False, []))
        assert r.can_invoke("invoker", "target") is False


class TestAgentRegistrySeal:
    def test_register_after_seal_raises(self):
        r = AgentRegistry()
        r.seal()
        with pytest.raises(RuntimeError, match="sealed"):
            r.register(AgentConfig(name="new", description="", type=AgentType.SUBAGENT))

    def test_seal_allows_primary(self):
        r = AgentRegistry()
        r.seal()
        r.register(AgentConfig(name="primary", description="", type=AgentType.PRIMARY))

    def test_seal_allows_offline_agent(self):
        r = AgentRegistry()
        r.seal()
        r.register(AgentConfig(name="offline-agent", description="", type=AgentType.SUBAGENT))

    def test_seal_allows_update_existing(self):
        r = AgentRegistry()
        cfg = AgentConfig(name="existing", description="old", type=AgentType.SUBAGENT)
        r.register(cfg)
        r.seal()
        r.register(AgentConfig(name="existing", description="new", type=AgentType.SUBAGENT))
        result = r.get("existing")
        assert result is not None
        assert result.description == "new"

    def test_register_before_seal_works_normally(self):
        r = AgentRegistry()
        r.register(AgentConfig(name="a", description="", type=AgentType.SUBAGENT))
        r.seal()
        r.register(AgentConfig(name="a", description="updated", type=AgentType.SUBAGENT))
        result = r.get("a")
        assert result is not None
        assert result.description == "updated"


class TestAgentRegistryGetBehavior:
    def _agent(self, name, agent_type, behavior=None):
        return AgentConfig(name=name, description="", type=agent_type, behavior=behavior)

    def test_returns_configured_behavior(self):
        from general_ludd.agents.behavior import AgentBehavior

        r = AgentRegistry()
        custom = AgentBehavior(role="custom")
        r.register(self._agent("a", AgentType.SUBAGENT, behavior=custom))
        assert r.get_behavior("a").role == "custom"

    def test_fallback_primary(self):
        r = AgentRegistry()
        r.register(self._agent("a", AgentType.PRIMARY))
        bh = r.get_behavior("a")
        assert bh.self_directed_work is True
        assert bh.completion_policy == "complete_all"

    def test_fallback_subagent(self):
        r = AgentRegistry()
        r.register(self._agent("a", AgentType.SUBAGENT))
        bh = r.get_behavior("a")
        assert bh.self_directed_work is False

    def test_unknown_agent_returns_subagent_default(self):
        r = AgentRegistry()
        bh = r.get_behavior("nonexistent")
        assert bh.self_directed_work is False


class TestAgentRegistryRenderBehaviorPrompt:
    def test_renders_for_known_agent(self):
        r = AgentRegistry()
        r.register(AgentConfig(name="build", description="", type=AgentType.PRIMARY))
        result = r.render_behavior_prompt("build", "do things")
        assert result is not None
        assert "build" in result
        assert "do things" in result

    def test_returns_none_for_unknown_agent(self):
        r = AgentRegistry()
        assert r.render_behavior_prompt("ghost", "task") is None


class TestDefaultRegistry:
    def test_has_five_agents(self):
        dr = default_registry()
        assert len(dr.list_agents()) == 5

    def test_contains_build(self):
        dr = default_registry()
        a = dr.get("build")
        assert a is not None
        assert a.type == AgentType.PRIMARY
        assert a.permissions.can_dispatch_subagents is True
        assert a.permissions.allowed_subagents == ["*"]

    def test_contains_plan(self):
        dr = default_registry()
        a = dr.get("plan")
        assert a is not None
        assert a.type == AgentType.PRIMARY
        assert a.permissions.can_edit is False
        assert a.permissions.allowed_subagents == ["explore"]

    def test_contains_explore(self):
        dr = default_registry()
        a = dr.get("explore")
        assert a is not None
        assert a.type == AgentType.SUBAGENT
        assert a.permissions.can_edit is False
        assert a.permissions.can_dispatch_subagents is False

    def test_contains_general(self):
        dr = default_registry()
        a = dr.get("general")
        assert a is not None
        assert a.type == AgentType.SUBAGENT
        assert a.permissions.can_edit is True
        assert a.permissions.can_bash is True

    def test_contains_research(self):
        dr = default_registry()
        a = dr.get("research")
        assert a is not None
        assert a.type == AgentType.SUBAGENT
        assert a.permissions.can_edit is False

    def test_is_sealed(self):
        dr = default_registry()
        with pytest.raises(RuntimeError, match="sealed"):
            dr.register(AgentConfig(name="hacker", description="", type=AgentType.SUBAGENT))

    def test_sealed_allows_primary(self):
        dr = default_registry()
        dr.register(AgentConfig(name="primary", description="", type=AgentType.PRIMARY))

    def test_max_concurrent_values(self):
        dr = default_registry()
        b = dr.get("build")
        assert b is not None
        assert b.max_concurrent == 3
        p = dr.get("plan")
        assert p is not None
        assert p.max_concurrent == 1
        e = dr.get("explore")
        assert e is not None
        assert e.max_concurrent == 5
        g = dr.get("general")
        assert g is not None
        assert g.max_concurrent == 3
        r = dr.get("research")
        assert r is not None
        assert r.max_concurrent == 3

    def test_can_invoke_build_to_any(self):
        dr = default_registry()
        assert dr.can_invoke("build", "explore") is True
        assert dr.can_invoke("build", "general") is True
        assert dr.can_invoke("build", "research") is True

    def test_can_invoke_plan_only_explore(self):
        dr = default_registry()
        assert dr.can_invoke("plan", "explore") is True
        assert dr.can_invoke("plan", "general") is False

    def test_can_invoke_subagents_cannot_dispatch(self):
        dr = default_registry()
        assert dr.can_invoke("explore", "general") is False
        assert dr.can_invoke("general", "explore") is False

    def test_list_subagents(self):
        dr = default_registry()
        subs = dr.list_subagents()
        sub_names = {a.name for a in subs}
        assert sub_names == {"explore", "general", "research"}
