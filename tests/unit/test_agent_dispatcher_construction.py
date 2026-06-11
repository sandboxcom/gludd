from __future__ import annotations

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry


class TestAgentDispatcherDaemonConstruction:
    def test_construct_with_registry(self):
        registry = AgentRegistry()
        dispatcher = AgentDispatcher(registry)
        assert dispatcher is not None
        assert dispatcher.active_count == 0

    def test_construct_rejects_model_gateway_kwarg(self):
        registry = AgentRegistry()
        with pytest.raises(TypeError):
            AgentDispatcher(registry=registry, model_gateway=None)

    def test_construct_rejects_session_factory_kwarg(self):
        registry = AgentRegistry()
        with pytest.raises(TypeError):
            AgentDispatcher(registry=registry, session_factory=None)

    def test_construct_with_custom_executor(self):
        registry = AgentRegistry()
        dispatcher = AgentDispatcher(registry, executor=lambda task: None)
        assert dispatcher is not None

    async def test_dispatch_noop_executor(self):
        registry = AgentRegistry()
        dispatcher = AgentDispatcher(registry)
        task = AgentTask(
            task_id="test-1",
            agent_name="nonexistent",
            description="test",
            prompt="test",
        )
        result = await dispatcher.dispatch_one(task)
        assert result.status == "failed"
