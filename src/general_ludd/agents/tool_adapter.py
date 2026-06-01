from __future__ import annotations

from general_ludd.agents.registry import AgentRegistry


class AgentToolAdapter:

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def list_agent_tools(self) -> list[dict[str, str]]:
        tools: list[dict[str, str]] = []
        for agent in self._registry.list_agents():
            tools.append({
                "name": f"dispatch_{agent.name}",
                "description": agent.description,
                "target_agent": agent.name,
                "type": "agent_dispatch",
            })
        return tools

    def get_agent_as_tool(self, agent_name: str) -> dict[str, str] | None:
        agent = self._registry.get(agent_name)
        if agent is None:
            return None
        return {
            "name": f"dispatch_{agent.name}",
            "description": agent.description,
            "target_agent": agent.name,
            "type": "agent_dispatch",
        }
