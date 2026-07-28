"""Tests for AgentCapabilities: construction, prepare_messages, within_budget, tool loops."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.agents.capabilities import AgentCapabilities


class TestAgentCapabilitiesConstruction:
    def test_default_construction(self):
        caps = AgentCapabilities()
        assert caps._max_tokens == 128000
        assert caps._compaction_threshold == 0.8
        assert caps._preserve_recent_count == 4
        assert caps.compactor is not None
        assert caps.token_window is not None
        assert caps.tool_adapter is not None
        assert caps.failover is not None

    def test_custom_token_budget(self):
        caps = AgentCapabilities(max_tokens=64000)
        assert caps._max_tokens == 64000

    def test_custom_threshold(self):
        caps = AgentCapabilities(compaction_threshold=0.9)
        assert caps._compaction_threshold == 0.9

    def test_custom_preserve_count(self):
        caps = AgentCapabilities(preserve_recent_count=2)
        assert caps._preserve_recent_count == 2

    def test_slm_compactor_is_none_without_gateway(self):
        caps = AgentCapabilities(use_slm_compaction=True, model_gateway=None)
        assert caps._slm_compactor is None

    def test_slm_compactor_is_none_without_flag(self):
        caps = AgentCapabilities(use_slm_compaction=False, model_gateway=MagicMock())
        assert caps._slm_compactor is None

    def test_no_slm_when_both_missing(self):
        caps = AgentCapabilities(use_slm_compaction=False, model_gateway=None)
        assert caps._slm_compactor is None

    def test_fallback_profiles_default(self):
        caps = AgentCapabilities()
        assert caps.failover._fallbacks == []

    def test_custom_fallback_profiles(self):
        caps = AgentCapabilities(fallback_profiles=["profile-a", "profile-b"])
        assert caps.failover._fallbacks == ["profile-a", "profile-b"]


class TestPrepareMessages:
    def test_returns_system_and_history(self):
        caps = AgentCapabilities()
        result = caps.prepare_messages(
            system_prompt="You are helpful.",
            history=[{"role": "user", "content": "Hello"}],
        )
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Hello"

    def test_handles_empty_history(self):
        caps = AgentCapabilities()
        result = caps.prepare_messages(
            system_prompt="System prompt",
            history=[],
        )
        assert len(result) == 1
        assert result[0]["role"] == "system"

    def test_preserves_assistant_role(self):
        caps = AgentCapabilities()
        result = caps.prepare_messages(
            system_prompt="System",
            history=[{"role": "assistant", "content": "Sure!"}],
        )
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Sure!"

    def test_default_role_is_user_for_missing_role(self):
        caps = AgentCapabilities()
        result = caps.prepare_messages(
            system_prompt="System",
            history=[{"content": "No role specified"}],
        )
        assert result[1]["role"] == "user"

    def test_preserves_system_role_in_history(self):
        caps = AgentCapabilities()
        result = caps.prepare_messages(
            system_prompt="Main system",
            history=[{"role": "system", "content": "Additional system"}],
        )
        assert result[1]["role"] == "system"

    def test_token_estimate_nonzero_for_content(self):
        caps = AgentCapabilities()
        result = caps.prepare_messages(
            system_prompt="A" * 1000,
            history=[],
        )
        assert result[0]["role"] == "system"


class TestWithinBudget:
    def test_within_budget_default(self):
        caps = AgentCapabilities(max_tokens=128000)
        within = caps.within_budget("agent1", "short prompt")
        assert within is True

    def test_custom_max_tokens_param(self):
        caps = AgentCapabilities(max_tokens=128000)
        within = caps.within_budget("agent1", "short prompt", max_tokens=1000)
        assert within is True

    def test_long_prompt_may_exceed(self):
        caps = AgentCapabilities(max_tokens=100)
        within = caps.within_budget("agent1", "x" * 500, max_tokens=50)
        assert within is False


class TestListAgentTools:
    def test_returns_list(self):
        caps = AgentCapabilities()
        tools = caps.list_agent_tools()
        assert isinstance(tools, list)


class TestMakeToolLoop:
    def test_returns_tool_call_loop(self):
        caps = AgentCapabilities()
        from general_ludd.execution.tool_loop import ToolCallLoop

        loop = caps.make_tool_loop(model_gateway=MagicMock())
        assert isinstance(loop, ToolCallLoop)

    def test_passes_budget_guard(self):
        caps = AgentCapabilities()
        budget_guard = MagicMock()
        loop = caps.make_tool_loop(model_gateway=MagicMock(), budget_guard=budget_guard)
        assert loop._budget_guard is budget_guard

    def test_passes_adversarial_detector(self):
        caps = AgentCapabilities()
        detector = MagicMock()
        loop = caps.make_tool_loop(model_gateway=MagicMock(), adversarial_detector=detector)
        assert loop._adversarial_detector is detector

    def test_passes_max_total_tokens(self):
        caps = AgentCapabilities()
        loop = caps.make_tool_loop(model_gateway=MagicMock(), max_total_tokens=50000)
        assert loop._max_total_tokens == 50000

    def test_passes_per_iteration_timeout(self):
        caps = AgentCapabilities()
        loop = caps.make_tool_loop(model_gateway=MagicMock(), per_iteration_timeout=30.0)
        assert loop._per_iteration_timeout == 30.0


class TestMakeLanggraphToolLoop:
    def test_valid_role_returns_loop(self):
        caps = AgentCapabilities()
        mcp_client = MagicMock()
        mcp_registry = MagicMock()
        loop = caps.make_langgraph_tool_loop(
            model_gateway=MagicMock(),
            role="operator",
            mcp_client=mcp_client,
            mcp_registry=mcp_registry,
        )
        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

        assert isinstance(loop, LangGraphAgentLoop)

    def test_raises_on_empty_role(self):
        caps = AgentCapabilities()
        with pytest.raises(ValueError, match="role is required"):
            caps.make_langgraph_tool_loop(model_gateway=MagicMock(), role="")

    def test_raises_on_none_role(self):
        caps = AgentCapabilities()
        with pytest.raises(ValueError, match="role is required"):
            caps.make_langgraph_tool_loop(model_gateway=MagicMock(), role=None)

    def test_raises_on_whitespace_role(self):
        caps = AgentCapabilities()
        with pytest.raises(ValueError, match="role is required"):
            caps.make_langgraph_tool_loop(model_gateway=MagicMock(), role="   ")

    def test_raises_on_unknown_role(self):
        caps = AgentCapabilities()
        with pytest.raises(ValueError, match="unknown role"):
            caps.make_langgraph_tool_loop(model_gateway=MagicMock(), role="nonexistent-role")

    def test_passes_budget_guard_through(self):
        caps = AgentCapabilities()
        budget_guard = MagicMock()
        loop = caps.make_langgraph_tool_loop(
            model_gateway=MagicMock(),
            role="operator",
            budget_guard=budget_guard,
        )
        assert loop._budget_guard is budget_guard

    def test_passes_chat_model_through(self):
        caps = AgentCapabilities()
        chat_model = MagicMock()
        loop = caps.make_langgraph_tool_loop(
            model_gateway=MagicMock(),
            role="operator",
            chat_model=chat_model,
        )
        assert loop._chat_model is chat_model


class TestMakeGraphGateway:
    def test_returns_langgraph_gateway(self):
        caps = AgentCapabilities()
        mock_gateway = MagicMock()
        gw = caps.make_graph_gateway(model_gateway=mock_gateway, enable_graph=True)
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        assert isinstance(gw, LangGraphGateway)
        assert gw._enable_graph is True

    def test_disabled_graph(self):
        caps = AgentCapabilities()
        gw = caps.make_graph_gateway(model_gateway=MagicMock(), enable_graph=False)
        from general_ludd.models.langgraph_gateway import LangGraphGateway

        assert isinstance(gw, LangGraphGateway)
        assert gw._enable_graph is False
