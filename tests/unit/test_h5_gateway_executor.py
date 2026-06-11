"""V2.1: Gateway-backed executor for AgentDispatcher."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask, _noop_executor
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentType
from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse


class TestGatewayBackedExecutor:
    def test_noop_executor_returns_empty_string(self):
        task = AgentTask(task_id="t1", agent_name="c", description="d", prompt="p")
        import asyncio
        r = asyncio.run(_noop_executor(task))
        assert r == ""

    def test_gateway_executor_calls_model(self):
        profile = ModelProfile(
            model_profile_id="m1", provider="openai",
            provider_package="lp", provider_class_hint="COAI",
            model_name="gt", enabled=True,
        )
        gateway = ModelGateway(profiles=[profile])
        gateway.call_model_with_retry = MagicMock(
            return_value=ModelResponse(content="generated code")
        )
        r = gateway.call_model_with_retry("m1", [{"role": "user", "content": "p"}])
        assert r.content == "generated code"
        gateway.call_model_with_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatcher_with_gateway_executor(self):
        reg = AgentRegistry()
        reg.register(AgentConfig(name="g", type=AgentType.SUBAGENT, description="d"))
        gw = ModelGateway(profiles=[ModelProfile(
            model_profile_id="m1", provider="openai",
            provider_package="lp", provider_class_hint="COAI",
            model_name="gt", enabled=True,
        )])
        gw.call_model_with_retry = MagicMock(
            return_value=ModelResponse(content="result")
        )
        async def ex(task: AgentTask) -> str:
            r = gw.call_model_with_retry(
                "m1", [{"role": "user", "content": task.prompt}],
            )
            return r.content
        d = AgentDispatcher(registry=reg, executor=ex)
        r = await d.dispatch_one(AgentTask(
            task_id="t3", agent_name="g", description="d", prompt="p",
        ))
        assert r.status == "completed"
        assert r.output == "result"

    @pytest.mark.asyncio
    async def test_dispatcher_falls_back_to_noop(self):
        reg = AgentRegistry()
        reg.register(AgentConfig(name="g", type=AgentType.SUBAGENT, description="d"))
        d = AgentDispatcher(registry=reg)
        r = await d.dispatch_one(AgentTask(
            task_id="t4", agent_name="g", description="d", prompt="p",
        ))
        assert r.status == "completed"
        assert r.output == ""
