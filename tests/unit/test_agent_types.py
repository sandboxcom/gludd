"""Tests for agents types: AgentType, AgentPermission, AgentConfig, AgentTask."""

from __future__ import annotations

from general_ludd.agents.types import (
    AgentConfig,
    AgentPermission,
    AgentTask,
    AgentType,
)


class TestAgentType:
    def test_primary_value(self):
        assert AgentType.PRIMARY.value == "primary"

    def test_subagent_value(self):
        assert AgentType.SUBAGENT.value == "subagent"

    def test_is_enum(self):
        assert isinstance(AgentType.PRIMARY, AgentType)
        assert isinstance(AgentType.SUBAGENT, AgentType)

    def test_from_string(self):
        assert AgentType("primary") is AgentType.PRIMARY
        assert AgentType("subagent") is AgentType.SUBAGENT


class TestAgentPermission:
    def test_defaults(self):
        perm = AgentPermission()
        assert perm.can_edit is False
        assert perm.can_bash is False
        assert perm.can_read is True
        assert perm.can_dispatch_subagents is False
        assert perm.allowed_subagents == []

    def test_custom_values(self):
        perm = AgentPermission(
            can_edit=True,
            can_bash=True,
            can_read=True,
            can_dispatch_subagents=True,
            allowed_subagents=["explore", "general"],
        )
        assert perm.can_edit is True
        assert perm.can_bash is True
        assert perm.can_read is True
        assert perm.can_dispatch_subagents is True
        assert perm.allowed_subagents == ["explore", "general"]

    def test_equality(self):
        p1 = AgentPermission(can_edit=True)
        p2 = AgentPermission(can_edit=True)
        assert p1 == p2

    def test_inequality(self):
        p1 = AgentPermission(can_edit=True)
        p2 = AgentPermission(can_edit=False)
        assert p1 != p2


class TestAgentConfig:
    def test_minimal_config(self):
        cfg = AgentConfig(
            name="test-agent",
            description="A test agent",
            type=AgentType.PRIMARY,
        )
        assert cfg.name == "test-agent"
        assert cfg.description == "A test agent"
        assert cfg.type is AgentType.PRIMARY
        assert cfg.model_profile is None
        assert cfg.prompt_profile is None
        assert cfg.max_steps == 10
        assert cfg.enabled is True
        assert cfg.max_concurrent == 1
        assert cfg.bind_tools_on_dispatch is True

    def test_default_permissions_factory(self):
        cfg = AgentConfig(
            name="test-agent",
            description="A test agent",
            type=AgentType.PRIMARY,
        )
        assert isinstance(cfg.permissions, AgentPermission)
        assert cfg.permissions.can_read is True

    def test_custom_permissions(self):
        perm = AgentPermission(can_edit=True, can_bash=True)
        cfg = AgentConfig(
            name="test-agent",
            description="A test agent",
            type=AgentType.SUBAGENT,
            max_steps=5,
            max_concurrent=3,
            permissions=perm,
        )
        assert cfg.permissions.can_edit is True
        assert cfg.permissions.can_bash is True
        assert cfg.max_steps == 5
        assert cfg.max_concurrent == 3
        assert cfg.type is AgentType.SUBAGENT

    def test_disabled_agent(self):
        cfg = AgentConfig(
            name="disabled-agent",
            description="A disabled agent",
            type=AgentType.PRIMARY,
            enabled=False,
        )
        assert cfg.enabled is False

    def test_behavior_is_none_by_default(self):
        cfg = AgentConfig(
            name="test-agent",
            description="A test agent",
            type=AgentType.PRIMARY,
        )
        assert cfg.behavior is None


class TestAgentTask:
    def test_minimal_task(self):
        task = AgentTask(
            task_id="task-001",
            agent_name="test-agent",
            description="Run tests",
            prompt="Run pytest",
        )
        assert task.task_id == "task-001"
        assert task.agent_name == "test-agent"
        assert task.description == "Run tests"
        assert task.prompt == "Run pytest"
        assert task.parent_task_id is None
        assert task.invoker_name == ""
        assert task.project_id is None
        assert task.depth == 0
        assert task.tools is None
        assert task.estimated_effort == "medium"

    def test_task_with_parent(self):
        task = AgentTask(
            task_id="task-002",
            agent_name="sub-agent",
            description="Sub task",
            prompt="Do sub work",
            parent_task_id="task-001",
            invoker_name="primary-agent",
            depth=1,
        )
        assert task.parent_task_id == "task-001"
        assert task.invoker_name == "primary-agent"
        assert task.depth == 1

    def test_task_with_project_id(self):
        task = AgentTask(
            task_id="task-003",
            agent_name="agent",
            description="Project task",
            prompt="Do project work",
            project_id="proj-123",
        )
        assert task.project_id == "proj-123"

    def test_task_with_tools(self):
        tools = [{"name": "read", "description": "Read files"}]
        task = AgentTask(
            task_id="task-004",
            agent_name="agent",
            description="Tool task",
            prompt="Use tools",
            tools=tools,
        )
        assert task.tools == tools

    def test_task_custom_effort(self):
        task = AgentTask(
            task_id="task-005",
            agent_name="agent",
            description="Big task",
            prompt="Do big work",
            estimated_effort="large",
        )
        assert task.estimated_effort == "large"

    def test_task_equality(self):
        t1 = AgentTask(task_id="t1", agent_name="a", description="d", prompt="p")
        t2 = AgentTask(task_id="t1", agent_name="a", description="d", prompt="p")
        assert t1 == t2

    def test_task_inequality(self):
        t1 = AgentTask(task_id="t1", agent_name="a", description="d", prompt="p")
        t2 = AgentTask(task_id="t2", agent_name="a", description="d", prompt="p")
        assert t1 != t2
