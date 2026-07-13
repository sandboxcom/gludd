"""H.6 — LANGGRAPH-FACTORY-ROLE-TRAP: make_langgraph_tool_loop must require role.

Ensures the factory cannot create agent loops without specifying a required role,
preventing privilege escalation via capability-gate bypass.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.agents.capabilities import AgentCapabilities
from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
from general_ludd.security.capability_lattice import _BUILTIN

_VALID_ROLES = list(_BUILTIN.keys())


class TestFactoryRequiresRole:
    def test_missing_role_raises_type_error(self):
        caps = AgentCapabilities()
        with pytest.raises(TypeError, match="role"):
            caps.make_langgraph_tool_loop(
                model_gateway=MagicMock(),
            )

    def test_none_role_raises_value_error(self):
        caps = AgentCapabilities()
        with pytest.raises(ValueError, match="role"):
            caps.make_langgraph_tool_loop(
                model_gateway=MagicMock(),
                role=None,
            )

    def test_empty_string_role_raises_value_error(self):
        caps = AgentCapabilities()
        with pytest.raises(ValueError, match="role"):
            caps.make_langgraph_tool_loop(
                model_gateway=MagicMock(),
                role="",
            )

    def test_whitespace_only_role_raises_value_error(self):
        caps = AgentCapabilities()
        with pytest.raises(ValueError, match="role"):
            caps.make_langgraph_tool_loop(
                model_gateway=MagicMock(),
                role="   ",
            )


class TestFactoryRejectsInvalidRoles:
    @pytest.mark.parametrize(
        "bad_role",
        [
            "admin",
            "root",
            "superuser",
            "hacker",
            "",
            "   ",
            "unknown_role",
            "any",
        ],
    )
    def test_invalid_role_raises_value_error(self, bad_role):
        caps = AgentCapabilities()
        with pytest.raises(ValueError):
            caps.make_langgraph_tool_loop(
                model_gateway=MagicMock(),
                role=bad_role,
            )


class TestFactoryAcceptsValidRoles:
    @pytest.mark.parametrize("role", _VALID_ROLES)
    def test_valid_role_constructs_loop(self, role):
        caps = AgentCapabilities()
        loop = caps.make_langgraph_tool_loop(
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
            role=role,
        )
        assert isinstance(loop, LangGraphAgentLoop)
        assert loop._role == role


class TestAgentLoopInheritsRole:
    def test_role_passed_through_to_loop(self):
        caps = AgentCapabilities()
        loop = caps.make_langgraph_tool_loop(
            model_gateway=MagicMock(),
            role="coder",
        )
        assert loop._role == "coder"

    def test_role_passed_to_check_dispatch(self):
        caps = AgentCapabilities()
        loop = caps.make_langgraph_tool_loop(
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
            role="operator",
        )
        assert loop._role == "operator"

    def test_default_role_not_accepted(self):
        caps = AgentCapabilities()
        with pytest.raises(TypeError, match="role"):
            caps.make_langgraph_tool_loop(model_gateway=MagicMock())
