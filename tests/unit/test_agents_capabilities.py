"""Structural tests for agents/capabilities.py — AgentCapabilities bundle."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.agents.capabilities import AgentCapabilities


class TestAgentCapabilitiesInit:
    def test_default_construction(self):
        cap = AgentCapabilities()
        assert cap._max_tokens == 128000
        assert cap._compaction_threshold == 0.8
        assert cap._preserve_recent_count == 4
        assert cap._compaction_level is None
        assert cap._slm_compactor is None
        assert cap._slm_summarize_fn is None

    def test_custom_params(self):
        cap = AgentCapabilities(max_tokens=64000, compaction_threshold=0.5, preserve_recent_count=2)
        assert cap._max_tokens == 64000
        assert cap._compaction_threshold == 0.5
        assert cap._preserve_recent_count == 2

    def test_has_token_window(self):
        cap = AgentCapabilities()
        assert cap.token_window is not None

    def test_has_tool_adapter(self):
        cap = AgentCapabilities()
        assert cap.tool_adapter is not None

    def test_has_failover(self):
        cap = AgentCapabilities()
        assert cap.failover is not None

    def test_has_compactor(self):
        cap = AgentCapabilities()
        assert cap.compactor is not None

    def test_slm_disabled_by_default(self):
        cap = AgentCapabilities()
        assert cap._slm_compactor is None
        assert cap._slm_summarize_fn is None

    def test_slm_enabled_with_gateway_and_flag(self):
        cap = AgentCapabilities(model_gateway=object(), use_slm_compaction=True)
        assert cap._slm_compactor is not None
        assert cap._slm_summarize_fn is not None


class TestPrepareMessages:
    def test_compacts_simple_history(self):
        cap = AgentCapabilities(max_tokens=100000)
        result = cap.prepare_messages("You are a helpful assistant.", [{"role": "user", "content": "hello"}])
        assert len(result) >= 1
        assert result[0]["role"] == "system"
        assert "You are a helpful assistant." in result[0]["content"]

    def test_empty_history(self):
        cap = AgentCapabilities()
        result = cap.prepare_messages("system", [])
        assert len(result) == 1  # system prompt only
        assert result[0]["role"] == "system"

    def test_missing_content_key(self):
        cap = AgentCapabilities()
        result = cap.prepare_messages("sys", [{"role": "user"}])
        assert len(result) == 2
        assert result[1]["content"] == ""


class TestWithinBudget:
    def test_within_default_budget(self):
        cap = AgentCapabilities(max_tokens=128000)
        assert cap.within_budget("test-agent", "short prompt") is True

    def test_explicit_cap_override(self):
        cap = AgentCapabilities(max_tokens=128000)
        huge = "x" * 500_000
        assert cap.within_budget("test-agent", huge, max_tokens=1000000) is True


class TestListAgentTools:
    def test_returns_list(self):
        cap = AgentCapabilities()
        tools = cap.list_agent_tools()
        assert isinstance(tools, list)


class TestMakeToolLoop:
    def test_returns_tool_loop(self):
        cap = AgentCapabilities()
        mock_gw = object()
        loop = cap.make_tool_loop(mock_gw)
        assert loop is not None

    def test_accepts_optional_params(self):
        cap = AgentCapabilities()
        mock_gw = object()
        loop = cap.make_tool_loop(
            mock_gw,
            max_total_tokens=50000,
            per_iteration_timeout=30.0,
            work_type_max_iterations={"code": 5},
        )
        assert loop is not None


class TestMakeLanggraphToolLoop:
    def test_requires_role(self):
        cap = AgentCapabilities()
        with pytest.raises(ValueError, match="role is required"):
            cap.make_langgraph_tool_loop(object(), "")

    def test_requires_non_empty_role(self):
        cap = AgentCapabilities()
        with pytest.raises(ValueError, match="role is required"):
            cap.make_langgraph_tool_loop(object(), "   ")

    def test_requires_known_role(self):
        cap = AgentCapabilities()
        with pytest.raises(ValueError, match="unknown role"):
            cap.make_langgraph_tool_loop(object(), "bogus-role-that-does-not-exist")


class TestMakeGraphGateway:
    @patch("general_ludd.models.langgraph_gateway.LangGraphGateway")
    @patch("general_ludd.scoring.engine.PromptScoringEngine")
    def test_returns_gateway(self, _, __):
        cap = AgentCapabilities()
        gw = cap.make_graph_gateway(object(), enable_graph=False)
        assert gw is not None
