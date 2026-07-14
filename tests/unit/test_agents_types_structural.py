"""Structural tests for agents/types.py — AgentType, AgentPermission, AgentConfig, AgentTask."""

from __future__ import annotations

from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType


class TestAgentType:
    def test_enum_values(self):
        assert AgentType.PRIMARY.value == "primary"
        assert AgentType.SUBAGENT.value == "subagent"

    def test_enum_members(self):
        members = list(AgentType)
        assert AgentType.PRIMARY in members
        assert AgentType.SUBAGENT in members
        assert len(members) == 2

    def test_enum_is_str(self):
        assert isinstance(AgentType.PRIMARY.value, str)


class TestAgentPermission:
    def test_defaults(self):
        perm = AgentPermission()
        assert perm.can_edit is False
        assert perm.can_bash is False
        assert perm.can_read is True
        assert perm.can_dispatch_subagents is False
        assert perm.allowed_subagents == []

    def test_custom_permission(self):
        perm = AgentPermission(can_edit=True, can_bash=True, allowed_subagents=["a1", "a2"])
        assert perm.can_edit is True
        assert perm.can_bash is True
        assert perm.can_read is True
        assert perm.allowed_subagents == ["a1", "a2"]

    def test_allowed_subagents_default_mutation_safe(self):
        p1 = AgentPermission()
        p2 = AgentPermission()
        p1.allowed_subagents.append("x")
        assert p2.allowed_subagents == []


class TestAgentConfig:
    def test_minimal_required(self):
        cfg = AgentConfig(name="test", description="desc", type=AgentType.SUBAGENT)
        assert cfg.name == "test"
        assert cfg.description == "desc"
        assert cfg.type == AgentType.SUBAGENT

    def test_defaults(self):
        cfg = AgentConfig(name="x", description="x", type=AgentType.PRIMARY)
        assert cfg.model_profile is None
        assert cfg.prompt_profile is None
        assert cfg.max_steps == 10
        assert cfg.max_concurrent == 1
        assert cfg.enabled is True
        assert cfg.bind_tools_on_dispatch is True
        assert cfg.behavior is None

    def test_permission_default(self):
        cfg = AgentConfig(name="x", description="x", type=AgentType.PRIMARY)
        assert cfg.permissions.can_edit is False
        assert cfg.permissions.can_read is True

    def test_custom_max_steps(self):
        cfg = AgentConfig(name="x", description="x", type=AgentType.SUBAGENT, max_steps=50)
        assert cfg.max_steps == 50


class TestAgentTask:
    def test_required_fields(self):
        task = AgentTask(task_id="t1", agent_name="a1", description="d", prompt="p")
        assert task.task_id == "t1"
        assert task.agent_name == "a1"
        assert task.description == "d"
        assert task.prompt == "p"

    def test_defaults(self):
        task = AgentTask(task_id="t1", agent_name="a1", description="d", prompt="p")
        assert task.parent_task_id is None
        assert task.invoker_name == ""
        assert task.project_id is None
        assert task.depth == 0
        assert task.tools is None
        assert task.estimated_effort == "medium"
