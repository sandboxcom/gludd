"""Verify make_langgraph_tool_loop threads budget_guard to LangGraphAgentLoop."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.agents.capabilities import AgentCapabilities


class TestLanggraphBudgetGuardThreading:
    def test_budget_guard_passed_through_to_loop(self):
        caps = AgentCapabilities()
        budget_guard = MagicMock()
        gateway = MagicMock()
        gateway.get_chat_model = MagicMock(return_value=MagicMock())
        loop = caps.make_langgraph_tool_loop(
            model_gateway=gateway,
            role="coder",
            mcp_client=MagicMock(),
            budget_guard=budget_guard,
        )
        assert loop._budget_guard is budget_guard

    def test_adversarial_detector_passed_through_to_loop(self):
        caps = AgentCapabilities()
        detector = MagicMock()
        gateway = MagicMock()
        gateway.get_chat_model = MagicMock(return_value=MagicMock())
        loop = caps.make_langgraph_tool_loop(
            model_gateway=gateway,
            role="coder",
            mcp_client=MagicMock(),
            adversarial_detector=detector,
        )
        assert loop._adversarial_detector is detector

    def test_max_total_tokens_passed_through_to_loop(self):
        caps = AgentCapabilities()
        gateway = MagicMock()
        gateway.get_chat_model = MagicMock(return_value=MagicMock())
        loop = caps.make_langgraph_tool_loop(
            model_gateway=gateway,
            role="coder",
            mcp_client=MagicMock(),
            max_total_tokens=50000,
        )
        assert loop._max_total_tokens == 50000

    def test_chat_model_resolved_from_gateway(self):
        caps = AgentCapabilities()
        chat_model = MagicMock()
        gateway = MagicMock()
        gateway.get_chat_model = MagicMock(return_value=chat_model)
        loop = caps.make_langgraph_tool_loop(
            model_gateway=gateway,
            role="coder",
            mcp_client=MagicMock(),
        )
        assert loop._chat_model is chat_model

    def test_none_defaults_when_budget_guard_not_provided(self):
        caps = AgentCapabilities()
        gateway = MagicMock()
        gateway.get_chat_model = MagicMock(return_value=MagicMock())
        loop = caps.make_langgraph_tool_loop(
            model_gateway=gateway,
            role="coder",
        )
        assert loop._budget_guard is None
