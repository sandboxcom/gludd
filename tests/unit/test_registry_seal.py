"""Tests for AgentRegistry.seal() — immutability after construction."""

from __future__ import annotations

import pytest

from general_ludd.agents.registry import AgentRegistry, default_registry
from general_ludd.agents.types import AgentConfig, AgentType


def _minimal_config(name: str) -> AgentConfig:
    return AgentConfig(name=name, description="", type=AgentType.SUBAGENT)


class TestAgentRegistrySeal:
    def test_unsealed_registry_allows_register(self) -> None:
        reg = AgentRegistry()
        reg.register(_minimal_config("agent-a"))
        assert reg.get("agent-a") is not None

    def test_sealed_registry_raises_on_register(self) -> None:
        reg = AgentRegistry()
        reg.register(_minimal_config("agent-a"))
        reg.seal()
        with pytest.raises(RuntimeError, match="registry is sealed"):
            reg.register(_minimal_config("agent-b"))

    def test_sealed_registry_preserves_existing_agents(self) -> None:
        reg = AgentRegistry()
        reg.register(_minimal_config("agent-a"))
        reg.seal()
        assert reg.get("agent-a") is not None

    def test_seal_is_idempotent(self) -> None:
        reg = AgentRegistry()
        reg.seal()
        reg.seal()  # second call must not raise
        with pytest.raises(RuntimeError, match="registry is sealed"):
            reg.register(_minimal_config("x"))

    def test_default_registry_is_sealed(self) -> None:
        reg = default_registry()
        with pytest.raises(RuntimeError, match="registry is sealed"):
            reg.register(_minimal_config("injected-agent"))

    def test_default_registry_agents_still_readable(self) -> None:
        reg = default_registry()
        assert reg.get("build") is not None
        assert reg.get("plan") is not None
        assert reg.get("explore") is not None
        assert reg.get("general") is not None
