"""Call graph — builds a relationship graph from extracted code blocks.

Models: calls, contains, inherits relationships between code blocks.
"""

from __future__ import annotations

from typing import Any


class CallGraph:
    """Directed graph of code block relationships."""

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, str]] = []
        self._parents: dict[str, str] = {}

    def build_from_blocks(self, blocks: list[dict[str, Any]]) -> None:
        for b in blocks:
            full_name = self._full_name(b)
            self._nodes[full_name] = b
            if b.get("parent"):
                self._parents[full_name] = b["parent"]
                self._edges.append({"from": b["parent"], "to": full_name, "relation": "contains"})

        for b in blocks:
            full_name = self._full_name(b)
            source = b.get("source", "")
            if source:
                for other_b in blocks:
                    other_name = self._full_name(other_b)
                    if other_name == full_name:
                        continue
                    if other_b["name"] in source and not self._has_edge(full_name, other_name):
                        self._edges.append({"from": full_name, "to": other_name, "relation": "calls"})

        for b in blocks:
            bases = b.get("base_classes", [])
            full_name = self._full_name(b)
            for base in bases:
                if base in self._nodes:
                    self._edges.append({"from": full_name, "to": base, "relation": "inherits"})

    @staticmethod
    def _full_name(block: dict[str, Any]) -> str:
        parent = block.get("parent")
        name = str(block.get("name", "unknown"))
        if parent:
            return f"{parent}.{name}"
        return name

    def has_node(self, name: str) -> bool:
        return name in self._nodes

    def get_callees(self, caller: str) -> list[str]:
        callees: list[str] = []
        for edge in self._edges:
            if edge["from"] == caller and edge["relation"] == "calls":
                callees.append(edge["to"])
        return callees

    def get_callers(self, callee: str) -> list[str]:
        callers: list[str] = []
        for edge in self._edges:
            if edge["to"] == callee and edge["relation"] == "calls":
                callers.append(edge["from"])
        return callers

    def is_subclass(self, child: str, parent: str) -> bool:
        for edge in self._edges:
            if edge["from"] == child and edge["to"] == parent and edge["relation"] == "inherits":
                return True
        return False

    def _has_edge(self, from_node: str, to_node: str) -> bool:
        return any(e["from"] == from_node and e["to"] == to_node for e in self._edges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [{"name": k, **v} for k, v in self._nodes.items()],
            "edges": self._edges,
        }
