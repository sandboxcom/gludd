"""Unit tests for the multitasking agent system."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask, AgentTaskResult
from general_ludd.agents.registry import AgentRegistry, default_registry
from general_ludd.agents.token_window import TokenWindowManager
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType


class TestAgentTypeEnum:
    def test_agent_type_enum(self):
        assert AgentType.PRIMARY.value == "primary"
        assert AgentType.SUBAGENT.value == "subagent"

    def test_agent_type_members(self):
        members = list(AgentType)
        assert len(members) == 2
        assert AgentType.PRIMARY in members
        assert AgentType.SUBAGENT in members


class TestAgentConfigDefaults:
    def test_agent_config_defaults(self):
        perm = AgentPermission()
        assert perm.can_edit is False
        assert perm.can_bash is False
        assert perm.can_read is True
        assert perm.can_dispatch_subagents is False
        assert perm.allowed_subagents == []

        cfg = AgentConfig(
            name="test-agent",
            description="A test agent",
            type=AgentType.SUBAGENT,
        )
        assert cfg.model_profile is None
        assert cfg.prompt_profile is None
        assert cfg.max_steps == 10
        assert cfg.permissions == AgentPermission()
        assert cfg.max_concurrent == 1
        assert cfg.enabled is True


class TestAgentRegistryRegisterAndGet:
    def test_agent_registry_register_and_get(self):
        reg = AgentRegistry()
        cfg = AgentConfig(
            name="my-agent",
            description="test",
            type=AgentType.SUBAGENT,
        )
        reg.register(cfg)
        result = reg.get("my-agent")
        assert result is not None
        assert result.name == "my-agent"

    def test_get_missing_returns_none(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None


class TestAgentRegistryListSubagents:
    def test_agent_registry_list_subagents(self):
        reg = AgentRegistry()
        reg.register(AgentConfig(name="p1", description="", type=AgentType.PRIMARY))
        reg.register(AgentConfig(name="s1", description="", type=AgentType.SUBAGENT))
        reg.register(AgentConfig(name="s2", description="", type=AgentType.SUBAGENT))

        subs = reg.list_subagents()
        names = [a.name for a in subs]
        assert "s1" in names
        assert "s2" in names
        assert "p1" not in names

    def test_list_agents_returns_all(self):
        reg = AgentRegistry()
        reg.register(AgentConfig(name="p1", description="", type=AgentType.PRIMARY))
        reg.register(AgentConfig(name="s1", description="", type=AgentType.SUBAGENT))
        assert len(reg.list_agents()) == 2


class TestAgentRegistryCanInvoke:
    def test_agent_registry_can_invoke_allows_matching_glob(self):
        reg = AgentRegistry()
        invoker = AgentConfig(
            name="build",
            description="",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
        )
        reg.register(invoker)
        reg.register(AgentConfig(name="explore", description="", type=AgentType.SUBAGENT))
        assert reg.can_invoke("build", "explore") is True

    def test_agent_registry_can_invoke_denies_non_matching(self):
        reg = AgentRegistry()
        invoker = AgentConfig(
            name="plan",
            description="",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=["explore"],
            ),
        )
        reg.register(invoker)
        reg.register(AgentConfig(name="general", description="", type=AgentType.SUBAGENT))
        assert reg.can_invoke("plan", "general") is False

    def test_can_invoke_denies_when_no_dispatch_permission(self):
        reg = AgentRegistry()
        invoker = AgentConfig(
            name="plan",
            description="",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(can_dispatch_subagents=False),
        )
        reg.register(invoker)
        reg.register(AgentConfig(name="explore", description="", type=AgentType.SUBAGENT))
        assert reg.can_invoke("plan", "explore") is False


class TestAgentDispatcher:
    @pytest.fixture()
    def registry(self):
        reg = AgentRegistry()
        reg.register(AgentConfig(
            name="build",
            description="",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=["*"],
            ),
        ))
        reg.register(AgentConfig(
            name="explore",
            description="",
            type=AgentType.SUBAGENT,
            max_concurrent=5,
            permissions=AgentPermission(can_read=True),
        ))
        reg.register(AgentConfig(
            name="general",
            description="",
            type=AgentType.SUBAGENT,
            max_concurrent=3,
            permissions=AgentPermission(can_edit=True, can_bash=True, can_read=True),
        ))
        return reg

    def test_agent_task_result_status(self):
        result = AgentTaskResult(
            task_id="t1",
            agent_name="explore",
            status="completed",
            output="done",
            artifacts=[],
            duration_seconds=0.1,
        )
        assert result.status == "completed"
        assert result.output == "done"

    @pytest.mark.asyncio()
    async def test_agent_dispatcher_dispatch_many_runs_concurrently(self, registry):
        execution_times: dict[str, float] = {}

        async def fake_executor(task: AgentTask) -> str:
            start = time.monotonic()
            await asyncio.sleep(0.05)
            execution_times[task.task_id] = time.monotonic() - start
            return f"result-{task.task_id}"

        dispatcher = AgentDispatcher(registry=registry, executor=fake_executor)

        tasks = [
            AgentTask(task_id="t1", agent_name="explore", description="d1", prompt="p1"),
            AgentTask(task_id="t2", agent_name="explore", description="d2", prompt="p2"),
            AgentTask(task_id="t3", agent_name="general", description="d3", prompt="p3"),
        ]

        start = time.monotonic()
        results = await dispatcher.dispatch_many(tasks)
        total = time.monotonic() - start

        assert len(results) == 3
        assert all(r.status == "completed" for r in results)
        assert total < 0.3, f"Expected concurrent execution, took {total:.2f}s"

    @pytest.mark.asyncio()
    async def test_agent_dispatcher_respects_max_concurrent(self, registry):
        active_count = 0
        max_observed = 0

        async def tracking_executor(task: AgentTask) -> str:
            nonlocal active_count, max_observed
            active_count += 1
            max_observed = max(max_observed, active_count)
            await asyncio.sleep(0.02)
            active_count -= 1
            return "ok"

        dispatcher = AgentDispatcher(registry=registry, executor=tracking_executor)

        tasks = [
            AgentTask(task_id=f"t{i}", agent_name="general", description="", prompt="")
            for i in range(6)
        ]

        results = await dispatcher.dispatch_many(tasks)
        assert len(results) == 6
        assert max_observed <= 3, f"Exceeded max_concurrent=3, saw {max_observed}"

    @pytest.mark.asyncio()
    async def test_agent_dispatcher_tracks_active_dispatches(self, registry):
        dispatcher = AgentDispatcher(
            registry=registry,
            executor=AsyncMock(return_value="ok"),
        )
        assert dispatcher.active_count == 0

        task = AgentTask(task_id="t1", agent_name="explore", description="d", prompt="p")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"
        assert result.agent_name == "explore"

    @pytest.mark.asyncio()
    async def test_dispatch_one_unknown_agent_fails(self, registry):
        dispatcher = AgentDispatcher(
            registry=registry,
            executor=AsyncMock(return_value="ok"),
        )
        task = AgentTask(task_id="t1", agent_name="nonexistent", description="", prompt="")
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"


class TestTokenWindowEstimateTokens:
    def test_token_window_estimate_tokens(self):
        mgr = TokenWindowManager(default_budget=10000)
        text = "Hello world, this is a test."
        tokens = mgr.estimate_tokens(text)
        assert tokens == len(text) // 4

    def test_estimate_tokens_empty(self):
        mgr = TokenWindowManager(default_budget=10000)
        assert mgr.estimate_tokens("") == 0


class TestTokenWindowCheckBudget:
    def test_token_window_check_budget_fits(self):
        mgr = TokenWindowManager(default_budget=10000)
        assert mgr.check_budget("explore", "short prompt", max_tokens=10000) is True

    def test_token_window_check_budget_exceeded(self):
        mgr = TokenWindowManager(default_budget=100)
        mgr.record_usage("explore", 80)
        long_prompt = "x" * 400
        assert mgr.check_budget("explore", long_prompt, max_tokens=100) is False


class TestTokenWindowRecordUsage:
    def test_token_window_record_usage(self):
        mgr = TokenWindowManager(default_budget=10000)
        mgr.record_usage("explore", 500)
        mgr.record_usage("explore", 300)
        assert mgr.get_remaining_budget("explore") == 9200

    def test_record_usage_unknown_agent(self):
        mgr = TokenWindowManager(default_budget=5000)
        assert mgr.get_remaining_budget("unknown") == 5000


class TestTokenWindowCompactContext:
    def test_token_window_compact_context(self):
        mgr = TokenWindowManager(default_budget=10000)
        mgr.record_usage("explore", 9500)
        summary = mgr.compact_context("explore")
        assert isinstance(summary, str)
        assert "compacted" in summary.lower() or len(summary) < 100

    def test_compact_context_under_budget(self):
        mgr = TokenWindowManager(default_budget=10000)
        mgr.record_usage("explore", 100)
        summary = mgr.compact_context("explore")
        assert summary == ""


class TestDefaultAgents:
    def test_default_agents_registered(self):
        reg = default_registry()
        agents = reg.list_agents()
        names = [a.name for a in agents]
        assert "build" in names
        assert "plan" in names
        assert "explore" in names
        assert "general" in names

    def test_build_agent_permissions(self):
        reg = default_registry()
        build = reg.get("build")
        assert build is not None
        assert build.type == AgentType.PRIMARY
        assert build.permissions.can_edit is True
        assert build.permissions.can_bash is True
        assert build.permissions.can_read is True
        assert build.permissions.can_dispatch_subagents is True
        assert build.max_concurrent == 1

    def test_plan_agent_readonly(self):
        reg = default_registry()
        plan = reg.get("plan")
        assert plan is not None
        assert plan.type == AgentType.PRIMARY
        assert plan.permissions.can_edit is False
        assert plan.permissions.can_bash is False
        assert plan.permissions.can_read is True

    def test_explore_subagent(self):
        reg = default_registry()
        explore = reg.get("explore")
        assert explore is not None
        assert explore.type == AgentType.SUBAGENT
        assert explore.max_concurrent == 5

    def test_general_subagent(self):
        reg = default_registry()
        general = reg.get("general")
        assert general is not None
        assert general.type == AgentType.SUBAGENT
        assert general.max_concurrent == 3
        assert general.permissions.can_edit is True

    def test_subagents_only(self):
        reg = default_registry()
        subs = reg.list_subagents()
        sub_names = [a.name for a in subs]
        assert "explore" in sub_names
        assert "general" in sub_names
        assert "build" not in sub_names
        assert "plan" not in sub_names
