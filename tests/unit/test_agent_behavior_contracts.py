"""Regression tests for extracted agent behavior configuration contracts."""

from __future__ import annotations

from general_ludd.agents import behavior as behavior_module
from general_ludd.agents import behavior_contracts
from general_ludd.agents.behavior import AgentBehavior, GuardrailConfig


def test_behavior_module_reexports_canonical_configuration_types() -> None:
    """Registry and caller imports keep stable class identities."""
    assert AgentBehavior is behavior_contracts.AgentBehavior
    assert GuardrailConfig is behavior_contracts.GuardrailConfig
    assert {"AgentBehavior", "GuardrailConfig"} <= set(behavior_module.__all__)
